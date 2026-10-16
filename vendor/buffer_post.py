#!/usr/bin/env python3
"""Create an X (Twitter) post on the ProflexFinance channel via the Buffer GraphQL API.

Buffer has no media-upload endpoint, so images must be passed as a public URL
(use host_image.sh first). Stdlib only — no pip installs.

Examples:
  # Post now with text only
  buffer_post.py --text "$(cat post.txt)"

  # Post now with a hosted image
  buffer_post.py --text-file post.txt --image-url https://raw.githubusercontent.com/.../img.png

  # Create a draft instead of publishing (safe test)
  buffer_post.py --text "hello" --mode draft

Modes: shareNow (default) | addToQueue | shareNext | draft | schedule (needs --due-at ISO8601)
"""
import argparse, json, os, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)


def load_config():
    with open(os.path.join(SKILL_DIR, "config.json")) as f:
        return json.load(f)


def load_token():
    tok = os.environ.get("BUFFER_ACCESS_TOKEN")
    if tok:
        return tok.strip()
    path = os.path.join(SKILL_DIR, ".secrets.env")
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("BUFFER_ACCESS_TOKEN="):
                return line.split("=", 1)[1].strip()
    sys.exit("ERROR: BUFFER_ACCESS_TOKEN not found in env or .secrets.env")


def gql(endpoint, token, query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        endpoint, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: HTTP {e.code}: {e.read().decode()}")


MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id text status dueAt channel { id name service } } }
    ... on MutationError { message }
  }
}
"""


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--text-file")
    g.add_argument("--thread-file",
                   help="file of an X thread; tweets separated by a line containing only ===. "
                        "First block is the root tweet (gets the image); the rest post as replies.")
    ap.add_argument("--image-url", help="public URL of an image to attach (to the root tweet)")
    ap.add_argument("--mode", default="shareNow",
                    choices=["shareNow", "addToQueue", "shareNext", "draft", "schedule"])
    ap.add_argument("--due-at", help="ISO8601 time, required when --mode schedule")
    ap.add_argument("--channel", help="override channel id")
    args = ap.parse_args()

    cfg = load_config()
    token = load_token()
    endpoint = cfg["buffer"]["endpoint"]
    channel = args.channel or cfg["buffer"]["x_channel_id"]

    thread_parts = None
    if args.thread_file:
        raw = open(args.thread_file, encoding="utf-8").read()
        blocks = [b.strip() for b in raw.split("\n===\n") if b.strip()]
        if not blocks:
            sys.exit("ERROR: thread file is empty")
        text = blocks[0]
        thread_parts = blocks[1:]
    elif args.text is not None:
        text = args.text
    else:
        text = open(args.text_file, encoding="utf-8").read()

    for i, t in enumerate([text] + (thread_parts or [])):
        if len(t) > 280:
            sys.exit(f"ERROR: tweet #{i+1} is {len(t)} chars (X limit 280). Split it further.\n---\n{t}")

    inp = {
        "channelId": channel,
        "text": text,
        "schedulingType": "automatic",
        "assets": [{"image": {"url": args.image_url}}] if args.image_url else [],
    }
    if thread_parts:
        inp["metadata"] = {"twitter": {"thread": [{"text": t, "assets": []} for t in thread_parts]}}
    if args.mode == "draft":
        inp["mode"] = "addToQueue"
        inp["saveToDraft"] = True
    elif args.mode == "schedule":
        if not args.due_at:
            sys.exit("ERROR: --mode schedule requires --due-at")
        inp["mode"] = "customScheduled"
        inp["dueAt"] = args.due_at
    else:
        inp["mode"] = args.mode

    resp = gql(endpoint, token, MUTATION, {"input": inp})
    if resp.get("errors"):
        sys.exit("ERROR: GraphQL: " + json.dumps(resp["errors"], indent=2))
    result = resp["data"]["createPost"]
    if result["__typename"] != "PostActionSuccess":
        sys.exit("ERROR: Buffer rejected post: " + result.get("message", str(result)))
    post = result["post"]
    print(json.dumps({
        "ok": True, "post_id": post["id"], "status": post.get("status"),
        "channel": post.get("channel", {}).get("name"), "mode": args.mode,
        "image_attached": bool(args.image_url),
    }, indent=2))


if __name__ == "__main__":
    main()
