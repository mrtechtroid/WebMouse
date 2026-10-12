"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .config import ConfigError, default_config_path, default_config_text, load_config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="webmouse",
        description="Use your phone or tablet as a touchpad, stylus pad and keyboard for this computer.",
    )
    p.add_argument("-c", "--config", metavar="FILE", help="path to a TOML config file")
    p.add_argument("--host", help="address to listen on (default 0.0.0.0)")
    p.add_argument("-p", "--port", type=int, help="port to listen on (default 8765)")
    p.add_argument("--pin", help="fixed PIN instead of a random one")
    p.add_argument("--backend", choices=["auto", "pynput", "uinput", "dummy"],
                   help="input backend (default auto)")
    p.add_argument("--tls", action="store_true", help="serve HTTPS with a self-signed certificate")
    p.add_argument("--no-stream", action="store_true", help="disable screen streaming")
    p.add_argument("--no-qr", action="store_true", help="do not print a QR code")
    p.add_argument("--init-config", nargs="?", const="", metavar="FILE",
                   help="write a commented default config file and exit")
    p.add_argument("--force", action="store_true", help="overwrite with --init-config")
    p.add_argument("-v", "--verbose", action="count", default=0, help="debug logging")
    p.add_argument("--version", action="version", version=f"webmouse {__version__}")
    return p


def _init_config(target: str, force: bool) -> int:
    path = Path(target).expanduser() if target else default_config_path()
    if path.exists() and not force:
        print(f"webmouse: {path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_config_text(), encoding="utf-8")
    print(f"Wrote {path}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)

    if args.init_config is not None:
        return _init_config(args.init_config, args.force)

    cli: dict = {}
    for section, key, value in (
        ("server", "host", args.host),
        ("server", "port", args.port),
        ("security", "pin", args.pin),
        ("input", "backend", args.backend),
        ("tls", "enabled", True if args.tls else None),
        ("stream", "enabled", False if args.no_stream else None),
    ):
        if value is not None:
            cli.setdefault(section, {})[key] = value

    try:
        cfg, path = load_config(args.config, cli)
    except ConfigError as exc:
        print(f"webmouse: {exc}", file=sys.stderr)
        return 2

    from .app import run

    return run(cfg, show_qr=not args.no_qr, config_path=path)


if __name__ == "__main__":
    sys.exit(main())
