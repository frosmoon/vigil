import os, json, time, threading, smtplib, socket, datetime as dt, logging
from email.message import EmailMessage
from zoneinfo import ZoneInfo
import boto3, requests, yaml
from flask import Flask, jsonify, send_from_directory

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("vigil")

AWS_SESSION = boto3.Session(
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
    region_name=os.environ.get("AWS_DEFAULT_REGION"),
)

CFG = yaml.safe_load(open(os.environ.get("CONFIG", "config.yml"))) or {}
LOGGER.info("monitoring regions: %s", CFG.get("regions", []))

TZ = ZoneInfo(CFG.get("tz", "Europe/Rome"))
DATA = os.environ.get("DATA", "/data"); 
os.makedirs(DATA, exist_ok=True)
EV_FILE = f"{DATA}/events.json"
app = Flask(__name__, static_folder="static")
_cl, STATE, LAST, MANUAL, STATS = {}, [], {}, {}, {}
POLL_ERROR, LAST_POLL = None, None
EVENTS = json.load(open(EV_FILE)) if os.path.exists(EV_FILE) else []
DAY_ALIASES = {"lun": "mon", "mar": "tue", "mer": "wed", "gio": "thu", "ven": "fri", "sab": "sat", "dom": "sun"}

def ec2(r): return _cl.setdefault(("e", r), AWS_SESSION.client("ec2", region_name=r))
def cw(r): return _cl.setdefault(("c", r), AWS_SESSION.client("cloudwatch", region_name=r))

def log(m, kind, note=""):
    EVENTS.append({"t": time.time(), "id": m["id"], "name": m["name"], "kind": kind, "note": note})
    del EVENTS[:-300]
    json.dump(EVENTS, open(EV_FILE, "w"))

def notify(subject, body):
    e = CFG.get("email")
    try:
        if e:
            msg = EmailMessage(); msg["Subject"], msg["From"], msg["To"] = subject, e["from"], e["to"]
            msg.set_content(body)
            with smtplib.SMTP(e["host"], e.get("port", 587), timeout=10) as s:
                s.starttls(); s.login(e["user"], os.environ["SMTP_PASS"]); s.send_message(msg)
        for url in CFG.get("webhooks", []):
            requests.post(url, data=body.encode(), headers={"Title": subject}, timeout=5)
    except Exception as ex:
        print("notify failed:", ex, flush=True)

def tcp_ok(host, port):
    try: socket.create_connection((host, port), 3).close(); return True
    except OSError: return False

def fetch():
    out = []
    for r in CFG["regions"]:
        pages = ec2(r).get_paginator("describe_instances").paginate()
        for page in pages:
          for res in page["Reservations"]:
            for i in res["Instances"]:
                st = i["State"]["Name"]
                if st == "terminated": continue
                tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                name = tags.get("Name", i["InstanceId"])
                mc = (CFG.get("machines") or {}).get(name, {})
                tcp_checks = mc.get("tcp_checks") or ([{"name": "TCP", "port": mc["tcp_port"], "required": True}]
                                                        if mc.get("tcp_port") else [])
                ifs = [{"id": n["NetworkInterfaceId"], "idx": n["Attachment"]["DeviceIndex"], "mac": n["MacAddress"],
                        "subnet": n["SubnetId"],
                        "private": [p["PrivateIpAddress"] for p in n["PrivateIpAddresses"]],
                        "public": [p["Association"]["PublicIp"] for p in n["PrivateIpAddresses"] if p.get("Association")],
                        "ipv6": [a["Ipv6Address"] for a in n.get("Ipv6Addresses", [])]}
                       for n in i.get("NetworkInterfaces", [])]
                host = i.get("PublicIpAddress") or i.get("PrivateIpAddress")
                up = st == "running"
                checks = [{"name": check_cfg.get("name", "TCP"), "port": check_cfg["port"],
                           "required": check_cfg.get("required", True),
                           "reach": tcp_ok(host, check_cfg["port"]) if up and host else None}
                          for check_cfg in tcp_checks]
                required_checks = [check for check in checks if check["required"]]
                reach = all(check["reach"] for check in required_checks) if required_checks else None
                ipv4_private = [address for interface in ifs for address in interface["private"]]
                ipv4_public = [address for interface in ifs for address in interface["public"]]
                ipv6 = [address for interface in ifs for address in interface["ipv6"]]
                out.append({"id": i["InstanceId"], "region": r, "name": mc.get("display_name", name),
                            "source_name": name, "description": mc.get("description", ""), "profile": mc,
                            "checks": checks, "tags": tags,
                            "type": i["InstanceType"], "architecture": i.get("Architecture"),
                            "platform": i.get("PlatformDetails", i.get("Platform", "Linux/UNIX")),
                            "monitoring": i.get("Monitoring", {}).get("State"),
                            "az": i["Placement"]["AvailabilityZone"], "state": st, "up": up, "reach": reach,
                            "launched": i["LaunchTime"].timestamp(), "public": i.get("PublicIpAddress"),
                            "private": i.get("PrivateIpAddress"), "ipv4_private": ipv4_private,
                            "ipv4_public": ipv4_public, "ipv6": ipv6, "ifs": ifs,
                            "restarts": mc.get("restarts", [])})
    return out

def stats(r, iid):
    c = STATS.get(iid)
    if c and time.time() - c[0] < 60: return c[1]
    q = [{"Id": k, "MetricStat": {"Metric": {"Namespace": "AWS/EC2", "MetricName": n,
          "Dimensions": [{"Name": "InstanceId", "Value": iid}]}, "Period": 300, "Stat": s}}
         for k, n, s in (("cpu", "CPUUtilization", "Average"), ("nin", "NetworkIn", "Sum"),
                         ("nout", "NetworkOut", "Sum"), ("chk", "StatusCheckFailed", "Maximum"))]
    now = dt.datetime.now(dt.timezone.utc)
    res = cw(r).get_metric_data(MetricDataQueries=q, StartTime=now - dt.timedelta(hours=3), EndTime=now,
                                ScanBy="TimestampAscending")
    d = {x["Id"]: [round(v, 2) for v in x["Values"]] for x in res["MetricDataResults"]}
    STATS[iid] = (time.time(), d); return d

def expected(m, now):
    if MANUAL.get(m["id"], 0) > time.time(): return True, ""
    g = CFG.get("grace_min", 10) * 60
    for t in m["restarts"]:
        h, mi = map(int, t.split(":"))
        if abs((now - now.replace(hour=h, minute=mi, second=0, microsecond=0)).total_seconds()) <= g:
            return True, ""
    maintenance_days = [DAY_ALIASES.get(d[:3].lower(), d[:3].lower()) for d in CFG.get("maintenance_days", [])]
    if now.strftime("%a").lower() in maintenance_days:
        return True, ""
    later = [t for t in sorted(m["restarts"]) if t > now.strftime("%H:%M")]
    msg = f"Yo, {m['name']} was not supposed to go down today at {now.strftime('%H:%M')}. "
    msg += f"It should have restarted at {later[0]}." if later else "It shouldn't go down at all today."
    return False, msg + " Today is not a maintenance day."

def check(m, now):
    L = LAST.setdefault(m["id"], {"up": m["up"], "since": time.time(), "exp": True, "alerted": False})
    if m["up"] == L["up"]:
        lim = CFG.get("max_down_min", 15)
        if not m["up"] and L["exp"] and not L["alerted"] and time.time() - L["since"] > lim * 60:
            notify(f"{m['name']} still down", f"{m['name']} was expected down but is still down after {lim} min.")
            L["alerted"] = True
        return
    ts = time.time(); L["up"] = m["up"]
    if not m["up"]:
        exp, why = expected(m, now)
        L.update(since=ts, exp=exp, alerted=not exp)
        log(m, "down", "expected" if exp else "UNEXPECTED")
        if not exp: notify(f"{m['name']} went down unexpectedly", why)
    else:
        mins = int((ts - L["since"]) / 60)
        log(m, "up", f"back after {mins} min")
        if L["alerted"]: notify(f"{m['name']} is back", f"{m['name']} is back up after {mins} min.")
        L.update(since=ts, alerted=False)

def loop():
    global STATE, POLL_ERROR, LAST_POLL
    while True:
        try:
            now = dt.datetime.now(TZ); ms = fetch()
            for m in ms:
                m["series"] = {}
                if m["state"] == "running":
                    try:
                        m["series"] = stats(m["region"], m["id"])
                    except Exception as ex:
                        print("metrics error:", ex, flush=True)
                check(m, now)
            STATE = ms
            POLL_ERROR = None
            LAST_POLL = time.time()
        except Exception as ex:
            POLL_ERROR = str(ex)
            LOGGER.exception("poll error")
        time.sleep(CFG.get("poll_sec", 30))

@app.get("/")
def index(): return send_from_directory("static", "index.html")

@app.get("/api/machines")
def machines():
    return jsonify({"machines": STATE, "events": EVENTS[-60:][::-1], "regions": CFG.get("regions", []),
                    "polled": LAST_POLL, "error": POLL_ERROR})

@app.get("/healthz")
def healthz():
    if LAST_POLL is None:
        return jsonify({"status": "starting", "polled": None}), 503
    if POLL_ERROR and not STATE:
        return jsonify({"status": "degraded", "error": POLL_ERROR}), 503
    return jsonify({"status": "degraded" if POLL_ERROR else "ok", "polled": LAST_POLL,
                    "machines": len(STATE), "error": POLL_ERROR})

@app.post("/api/machines/<region>/<iid>/<action>")
def act(region, iid, action):
    c = ec2(region)
    fn = {"start": c.start_instances, "stop": c.stop_instances, "reboot": c.reboot_instances}.get(action)
    m = next((x for x in STATE if x["id"] == iid), None)
    if not fn or not m: return "no", 400
    MANUAL[iid] = time.time() + 900          # your own click is not an incident
    fn(InstanceIds=[iid]); log(m, "action", action)
    return "ok"

@app.post("/api/test-alert")
def test(): notify("Fleet dashboard test", "Yo, alerts work."); return "ok"

threading.Thread(target=loop, daemon=True).start()
if __name__ == "__main__": app.run(host="0.0.0.0", port=8080)
