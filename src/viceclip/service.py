"""Manage a single local autopilot process and optional OS restart service."""
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time


def paths(config_path):
    config=Path(config_path).expanduser().resolve()
    home=config.parent/"run"
    return config,home,home/"service.json",home/"autopilot.log"


def command_for(config_path, execute=None):
    command=[sys.executable,"-m","viceclip.cli","autopilot","run","--config",str(Path(config_path).resolve())]
    if execute is True:command.append("--execute")
    elif execute is False:command.append("--preview")
    return command


def _label(config_path):
    return "com.leo.viceclip."+hashlib.sha256(str(Path(config_path).resolve()).encode()).hexdigest()[:10]


def _owned_process(pid,config):
    try:
        result=subprocess.run(["ps","-p",str(pid),"-o","args="],capture_output=True,text=True,check=True)
        return "viceclip.cli autopilot run" in result.stdout and str(config) in result.stdout
    except (OSError,subprocess.CalledProcessError):return False


def service_status(config_path):
    config,home,record,log=paths(config_path)
    data={"running":False,"mode":"stopped","log":str(log)}
    if record.exists():
        try:data.update(json.loads(record.read_text()))
        except (OSError,ValueError):return data
    pid=data.get("pid")
    if pid:data["running"]=_owned_process(pid,config)
    if data.get("provider")=="launchd":
        r=subprocess.run(["launchctl","print",f"gui/{os.getuid()}/{_label(config)}"],capture_output=True,text=True)
        data["running"]=r.returncode==0 and "state = running" in r.stdout
    elif data.get("provider")=="systemd":
        r=subprocess.run(["systemctl","--user","is-active",str(data["unit"])],capture_output=True,text=True)
        data["running"]=r.returncode==0 and r.stdout.strip()=="active"
    return data


def start(config_path, *, execute=None, persistent=False):
    config,home,record,log=paths(config_path)
    if not config.is_file():raise ValueError("Сначала запустите viceclip setup")
    if service_status(config)["running"]:return {"running":True,"message":"Автопилот уже работает"}
    home.mkdir(parents=True,exist_ok=True)
    os.chmod(home,0o700)
    command=command_for(config,execute)
    info={"running":True,"provider":"process","started_at":time.time(),"config":str(config),"log":str(log)}
    if persistent and platform.system()=="Darwin":
        import plistlib
        plist=Path.home()/"Library/LaunchAgents"/(_label(config)+".plist")
        plist.parent.mkdir(parents=True,exist_ok=True)
        definition={"Label":_label(config),"ProgramArguments":command,"WorkingDirectory":str(config.parent),
            "RunAtLoad":True,"KeepAlive":True,"ThrottleInterval":30,
            "StandardOutPath":str(log),"StandardErrorPath":str(log),
            "EnvironmentVariables":{"PATH":str(Path(sys.executable).parent)+":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}}
        plist.write_bytes(plistlib.dumps(definition))
        subprocess.run(["launchctl","bootout",f"gui/{os.getuid()}",str(plist)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl","bootstrap",f"gui/{os.getuid()}",str(plist)],check=True)
        info.update(provider="launchd",unit=str(plist),mode="persistent")
    elif persistent and platform.system()=="Linux":
        unit_name="viceclip-"+_label(config).rsplit(".",1)[-1]+".service"
        unit=Path.home()/".config/systemd/user"/unit_name
        unit.parent.mkdir(parents=True,exist_ok=True)
        # systemd has its own quoting; json strings preserve paths containing spaces.
        exec_line=" ".join(json.dumps(str(x)) for x in command)
        definition="[Unit]\nDescription=Vice Clip Factory autopilot\nAfter=network-online.target\n\n[Service]\nType=simple\n"
        definition+="ExecStart="+exec_line+"\nWorkingDirectory="+str(config.parent)+"\nRestart=on-failure\nRestartSec=30\n"
        definition+="Environment=PATH="+str(Path(sys.executable).parent)+":/usr/local/bin:/usr/bin:/bin\n\n[Install]\nWantedBy=default.target\n"
        unit.write_text(definition)
        subprocess.run(["systemctl","--user","daemon-reload"],check=True)
        subprocess.run(["systemctl","--user","enable","--now",unit_name],check=True)
        info.update(provider="systemd",unit=unit_name,mode="persistent")
    else:
        env=dict(os.environ)
        # Editable installs work from any directory; keep source checkouts usable too.
        root=Path(__file__).resolve().parents[2]
        env["PYTHONPATH"]=str(root/"src")+(os.pathsep+env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        with open(log,"ab",buffering=0) as stream:
            process=subprocess.Popen(command,cwd=config.parent,env=env,stdin=subprocess.DEVNULL,
                stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        info.update(pid=process.pid,mode="background")
    from .onboarding import private_json
    private_json(record,info)
    return info


def stop(config_path):
    config,home,record,log=paths(config_path)
    info=service_status(config)
    if info.get("provider")=="launchd":
        subprocess.run(["launchctl","bootout",f"gui/{os.getuid()}",info["unit"]],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        Path(info["unit"]).unlink(missing_ok=True)
    elif info.get("provider")=="systemd":
        subprocess.run(["systemctl","--user","disable","--now",info["unit"]],check=False)
        (Path.home()/".config/systemd/user"/info["unit"]).unlink(missing_ok=True)
        subprocess.run(["systemctl","--user","daemon-reload"],check=False)
    elif info.get("pid") and _owned_process(info["pid"],config):
        os.kill(info["pid"],signal.SIGTERM)
        for _ in range(30):
            if not _owned_process(info["pid"],config):break
            time.sleep(.1)
        if _owned_process(info["pid"],config):
            return {"running":True,"stopping":True,"message":"Завершает текущую обработку. Новые задачи остановлены."}
    record.unlink(missing_ok=True)
    return {"running":False,"message":"Автопилот остановлен"}


def logs(config_path,lines=40):
    config,home,record,log=paths(config_path)
    if not log.exists():return "Журнал появится после первого запуска"
    return "\n".join(log.read_text(errors="replace").splitlines()[-max(1,min(lines,1000)):])
