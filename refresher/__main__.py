"""python -m refresher run | once | pair <code> | status"""
import argparse
import importlib.util
import logging
import logging.handlers
import sys
from datetime import datetime, timezone

from . import config
from .version import VERSION


def _logging(base):
    root = logging.getLogger("jobbuddy.refresher")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        paths = config.paths(base)
        paths["logs"].mkdir(parents=True, exist_ok=True)
        fileh = logging.handlers.RotatingFileHandler(paths["logs"] / "refresher.log", maxBytes=1_000_000, backupCount=5)
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError:
        pass
    logging.getLogger("jobbuddy").setLevel(logging.INFO)
    return root


def _connection(base):
    conn = config.load(base)
    if conn is None:
        sys.exit("This computer isn't connected to Job Buddy yet. Run the installer again and paste the connection code.")
    return conn


def _agent(base):
    from .agent import Agent, IngestRunner
    from .client import Client
    conn = _connection(base)
    config.setup_environment(conn, base)
    return Agent(conn, Client(conn["site_url"], conn["token"]), IngestRunner(), base)


def cmd_pair(args):
    try:
        site, token, name = config.parse_connection_code(args.code)
    except ValueError as e:
        sys.exit(str(e))
    from .client import Client, ClientError
    try:
        Client(site, token).checkin({"version": VERSION, "state": "idle", "host": "pairing"})
    except ClientError as e:
        sys.exit(f"Couldn't connect: {e}")
    config.save(site, token, name, args.home)
    print(f"Connected to {site} as “{name}”.")


def cmd_run(args):
    _logging(args.home)
    _agent(args.home).run_forever()


def cmd_once(args):
    """A refresh right now, in the foreground, so you can watch it. Does not wait for the schedule."""
    import time
    _logging(args.home)
    agent = _agent(args.home)
    agent.tick()
    agent._start(datetime.now(timezone.utc), "requested")
    while agent.runner.running():
        p = agent.runner.progress()
        print(f"{p['phase']}… {p['found']} found, {p['new']} new  {p['now']}", flush=True)
        agent.tick()
        time.sleep(10)
    agent.tick()
    print("Finished:", agent.state().get("last_run"))


def cmd_status(args):
    conn, state = config.load(args.home), config.load_state(args.home)
    print(f"Job Buddy Refresher {VERSION}")
    print(f"Python {sys.version.split()[0]} · fetching tools (JobSpy): {'installed' if importlib.util.find_spec('jobspy') else 'MISSING'}")
    if conn is None:
        print("Not connected yet. Run the installer and paste the connection code.")
        return
    print(f"Website: {conn['site_url']}  ·  this computer is “{conn['name']}”")
    last = state.get("last_run")
    print("Last refresh:", f"{last['status']} at {last['at'][:16].replace('T', ' ')} UTC. {last.get('message', '')}" if last else "none yet")
    print("Logs:", config.paths(args.home)["logs"] / "refresher.log")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m refresher")
    parser.add_argument("--home", help="where the refresher keeps its files (default ~/job-buddy-refresher)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run for good: check in, refresh every morning").set_defaults(fn=cmd_run)
    sub.add_parser("once", help="refresh right now and watch it").set_defaults(fn=cmd_once)
    pair = sub.add_parser("pair", help="connect to the website with a connection code")
    pair.add_argument("code")
    pair.set_defaults(fn=cmd_pair)
    sub.add_parser("status", help="show how this computer is set up").set_defaults(fn=cmd_status)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
