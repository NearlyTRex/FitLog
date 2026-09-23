import argparse
import getpass
import io
import sys

from . import auth
from .catalog import load_catalog
from .config import Config
from .db import Database


def _read_password():
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise auth.AuthError("Passwords do not match")
    return password


def _show_totp(username, secret):
    uri = auth.provisioning_uri(username, secret)
    print("\nScan this with your authenticator app:\n")
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(uri)
        out = io.StringIO()
        qr.print_ascii(out=out, invert=True)
        print(out.getvalue())
    except ImportError:
        pass
    print(f"Or enter this key manually: {secret}")
    print(f"URI: {uri}\n")


def cmd_user(args, config):
    db = Database(config.db_path)
    if args.action == "create":
        secret = auth.create_user(db, args.username, _read_password())
        print(f"Created user '{args.username}'.")
        _show_totp(args.username, secret)
    elif args.action == "reset-password":
        auth.set_password(db, args.username, _read_password())
        print("Password changed; existing sessions were signed out.")
    elif args.action == "reset-totp":
        secret = auth.reset_totp(db, args.username)
        print("Authenticator reset; existing sessions were signed out.")
        _show_totp(args.username, secret)
    return 0


def cmd_catalog_check(args, config):
    catalog = load_catalog(args.dir or config.catalog_dir)
    for error in catalog.errors:
        print(f"error: {error}", file=sys.stderr)
    print(f"{len(catalog.foods)} foods, {len(catalog.exercises)} exercises, {len(catalog.errors)} errors")
    return 1 if catalog.errors else 0


def cmd_serve(args, config):
    import uvicorn
    from .web import create_app
    uvicorn.run(create_app(config), host=args.host, port=args.port, proxy_headers=False)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fitlog", description="Personal food and exercise tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    user = sub.add_parser("user", help="Create the user or reset their credentials")
    user.add_argument("action", choices=["create", "reset-password", "reset-totp"])
    user.add_argument("username")
    user.set_defaults(func=cmd_user)

    check = sub.add_parser("check", help="Validate the catalog files")
    check.add_argument("dir", nargs="?", help="Catalog directory (defaults to FITLOG_CATALOG_DIR)")
    check.set_defaults(func=cmd_catalog_check)

    serve = sub.add_parser("serve", help="Run the web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args, Config.from_env())
    except auth.AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
