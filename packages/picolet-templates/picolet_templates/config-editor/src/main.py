# config-editor — schema-driven config file editor (picolet example).
#
# IPC commands:
#   list_dir(path)                        -> [{name, is_dir}]
#   list_schemas()                        -> [schema_name, ...]
#   load(path)                            -> {format, document, schema_hint}
#   validate(format, document, schema_name) -> [{path, message}]
#   save(path, format, document)          -> {diff: [...], ok: True}
#
# FR-EX-3 (config-editor template), FR-EX-5, FR-EX-6.
import picolet
import picolet_ui as ui
import config_store as store


@picolet.command
async def list_dir(args):
    path = args.get("path", "") if isinstance(args, dict) else str(args)
    try:
        return store.list_dir(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@picolet.command
async def list_schemas(args):
    try:
        return store.list_schemas()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@picolet.command
async def load(args):
    path = args.get("path") if isinstance(args, dict) else str(args)
    try:
        return store.load(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@picolet.command
async def validate(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be a dict"}
    fmt = args.get("format", "")
    document = args.get("document", {})
    schema_name = args.get("schema_name", "")
    try:
        errors = store.validate(fmt, document, schema_name)
        return {"errors": errors, "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@picolet.command
async def save(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be a dict"}
    path = args.get("path")
    fmt = args.get("format")
    document = args.get("document", {})
    try:
        return store.save(path, fmt, document)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    app = ui.Application()
    app.run()


main()
