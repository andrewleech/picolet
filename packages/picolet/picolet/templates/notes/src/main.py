# notes — markdown notes app (picolet example).
#
# IPC commands:
#   list_notes()                     -> list of {slug, title, created, updated}
#   load_note({slug})                -> {slug, title, created, updated, body}
#   save_note({slug, body})          -> {slug, title, created, updated}
#   rename_note({slug, title})       -> {slug, title, created, updated}
#   create_note({title})             -> {slug, title, created, updated}
#   delete_note({slug})              -> {"ok": True}
import picolet
import picolet_ui as ui
import notes_store as store


@picolet.command
async def list_notes(args):
    return store.list_notes()


@picolet.command
async def load_note(args):
    slug = args.get("slug") if isinstance(args, dict) else args
    try:
        return store.load_note(slug)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}


@picolet.command
async def save_note(args):
    slug = args.get("slug") if isinstance(args, dict) else None
    body = args.get("body", "") if isinstance(args, dict) else ""
    try:
        return store.save_note(slug, body)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}


@picolet.command
async def rename_note(args):
    slug = args.get("slug") if isinstance(args, dict) else None
    title = args.get("title", "Untitled") if isinstance(args, dict) else "Untitled"
    try:
        return store.rename_note(slug, title)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}


@picolet.command
async def create_note(args):
    title = args.get("title", "Untitled") if isinstance(args, dict) else str(args)
    return store.create_note(title)


@picolet.command
async def delete_note(args):
    slug = args.get("slug") if isinstance(args, dict) else args
    try:
        store.delete_note(slug)
        return {"ok": True}
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}


def main():
    app = ui.Application()
    app.run()


main()
