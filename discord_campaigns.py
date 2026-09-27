#!/usr/bin/env python3
"""
Build a table of the campaigns posted in a Discord channel, categorised by
system, language and whatever other "Key: value" fields the posts contain.

Works with both plain text channels (one message per campaign) and forum
channels (one thread per campaign, forum tags used as categories).

Requirements: Python 3.8+, no third-party packages.

Authentication
--------------
Create a bot at https://discord.com/developers/applications, enable the
"Message Content Intent" (Bot tab), invite it to the server with the
"View Channels" and "Read Message History" permissions, then:

    export DISCORD_TOKEN="your-bot-token"
    python3 discord_campaigns.py

Useful options:

    --format markdown|csv|html|json   output format (default: markdown)
    -o campaigns.md                   write to a file instead of stdout
    --group-by system language        nesting of the grouped sections
    --dump raw.json                   save the raw API data
    --from-json raw.json              re-run offline from a previous dump
"""

import argparse
import csv
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict

API = "https://discord.com/api/v10"
DEFAULT_CHANNEL = "1017773794699522098"

# --------------------------------------------------------------------------
# Field recognition
# --------------------------------------------------------------------------

# Canonical field -> aliases (lowercase) that may appear before a ":" in posts.
FIELD_ALIASES = {
    "system": ["system", "sistema", "game system", "ruleset", "rules",
               "regolamento", "gioco", "game", "edition", "edizione", "rpg"],
    "language": ["language", "languages", "lingua", "lingue", "lang",
                 "idioma", "sprache", "langue"],
    "title": ["title", "titolo", "campaign", "campagna", "campaign name",
              "nome campagna", "name", "nome", "adventure", "avventura"],
    "gm": ["gm", "dm", "master", "game master", "dungeon master", "narratore",
           "keeper", "storyteller", "host", "hosted by"],
    "schedule": ["schedule", "when", "day", "days", "time", "date",
                 "quando", "giorno", "giorni", "orario", "ora", "frequency",
                 "frequenza", "sessions", "sessioni", "timezone"],
    "platform": ["platform", "piattaforma", "vtt", "where", "dove", "location",
                 "luogo", "online/offline", "tools", "strumenti"],
    "players": ["players", "giocatori", "slots", "seats", "posti",
                "party size", "number of players", "player count"],
    "level": ["level", "livello", "starting level", "livello iniziale",
              "experience", "esperienza", "experience level"],
    "status": ["status", "stato", "recruiting", "open", "reclutamento"],
    "setting": ["setting", "ambientazione", "world", "mondo", "genre", "genere",
                "tone", "tono", "theme", "tema"],
    "cost": ["cost", "costo", "price", "prezzo", "paid", "free"],
}
ALIAS_TO_FIELD = {a: f for f, aliases in FIELD_ALIASES.items() for a in aliases}

# Keyword -> normalised system name, used when no explicit field / tag exists.
SYSTEM_KEYWORDS = [
    (r"\bd\s*&\s*d\s*5\.?5|\bdnd\s*2024|\bd&d\s*2024|one\s*d&d", "D&D 5.5e (2024)"),
    (r"\bd\s*&\s*d\s*5e?\b|\bdnd\s*5e?\b|\b5e\b|fifth edition|quinta edizione", "D&D 5e"),
    (r"\bd\s*&\s*d\s*3\.5|\bdnd\s*3\.5|\b3\.5e?\b", "D&D 3.5e"),
    (r"\bad&d\b|advanced dungeons", "AD&D"),
    (r"\bd\s*&\s*d\b|\bdnd\b|dungeons\s*(&|and|e)\s*dragons", "D&D"),
    (r"pathfinder\s*2|\bpf2e?\b", "Pathfinder 2e"),
    (r"pathfinder|\bpf1e?\b", "Pathfinder 1e"),
    (r"starfinder", "Starfinder"),
    (r"call of cthulhu|richiamo di cthulhu|\bcoc\b", "Call of Cthulhu"),
    (r"delta green", "Delta Green"),
    (r"vampire|vampiri|\bvtm\b|\bv5\b", "Vampire: The Masquerade"),
    (r"werewolf|\bw5\b", "Werewolf: The Apocalypse"),
    (r"world of darkness|\bwod\b|chronicles of darkness", "World of Darkness"),
    (r"cyberpunk\s*red", "Cyberpunk RED"),
    (r"cyberpunk", "Cyberpunk"),
    (r"shadowrun", "Shadowrun"),
    (r"blades in the dark|\bbitd\b", "Blades in the Dark"),
    (r"powered by the apocalypse|\bpbta\b|apocalypse world", "PbtA"),
    (r"dungeon world", "Dungeon World"),
    (r"monster of the week|\bmotw\b", "Monster of the Week"),
    (r"savage worlds", "Savage Worlds"),
    (r"\bfate\b", "Fate"),
    (r"\bgurps\b", "GURPS"),
    (r"warhammer fantasy|\bwfrp\b", "Warhammer Fantasy RP"),
    (r"wrath\s*&?\s*glory|dark heresy|rogue trader|warhammer 40", "Warhammer 40k RP"),
    (r"star wars", "Star Wars RPG"),
    (r"alien rpg|\balien\b", "Alien RPG"),
    (r"mothership", "Mothership"),
    (r"\bosr\b|old school essentials|\bose\b", "OSR / OSE"),
    (r"shadowdark", "Shadowdark"),
    (r"dragonbane|drakar", "Dragonbane"),
    (r"forbidden lands", "Forbidden Lands"),
    (r"vaesen", "Vaesen"),
    (r"tales from the loop", "Tales from the Loop"),
    (r"mork borg|mörk borg", "Mörk Borg"),
    (r"cypher|numenera", "Cypher System"),
    (r"13th age", "13th Age"),
    (r"lancer", "Lancer"),
    (r"daggerheart", "Daggerheart"),
    (r"draw steel|mcdm", "Draw Steel"),
    (r"one ring|unico anello", "The One Ring"),
    (r"legend of the five rings|\bl5r\b", "Legend of the Five Rings"),
    (r"runequest", "RuneQuest"),
    (r"traveller", "Traveller"),
    (r"kids on bikes", "Kids on Bikes"),
    (r"homebrew|sistema proprio|custom system", "Homebrew"),
]

LANGUAGE_KEYWORDS = [
    (r"🇮🇹|\bitalian[oa]?\b|\bitaliano\b|\bita\b|\bit\b", "Italian"),
    (r"🇬🇧|🇺🇸|🇺🇲|\benglish\b|\binglese\b|\beng?\b", "English"),
    (r"🇩🇪|\bgerman\b|\bdeutsch\b|\btedesco\b", "German"),
    (r"🇫🇷|\bfrench\b|\bfran[cç]ais\b|\bfrancese\b", "French"),
    (r"🇪🇸|\bspanish\b|\bespa[nñ]ol\b|\bspagnolo\b", "Spanish"),
    (r"🇵🇹|🇧🇷|\bportuguese\b|\bportugu[eê]s\b|\bportoghese\b", "Portuguese"),
    (r"🇳🇱|\bdutch\b|\bnederlands\b|\bolandese\b", "Dutch"),
    (r"🇵🇱|\bpolish\b|\bpolski\b", "Polish"),
    (r"🇷🇺|\brussian\b|\bрусский\b", "Russian"),
]

UNKNOWN = "—"

KV_LINE = re.compile(
    r"^[\s>*_~`|\-•·▸►➤⮞→🔹🔸◆◇●○■□\u2600-\u27BF\U0001F300-\U0001FAFF]*"
    r"(?P<key>[^\W\d_][\w &/'().-]{0,30}?)"
    r"[\s*_`]*[:：=][\s*_`]*"
    r"(?P<val>.+?)\s*$",
    re.UNICODE,
)
MARKDOWN = re.compile(r"(\*\*|__|~~|`|\|\||^#+\s*|^>\s*)")
URL = re.compile(r"https?://\S+")


def clean(text):
    text = MARKDOWN.sub("", text or "")
    text = re.sub(r"<a?:(\w+):\d+>", r"\1", text)  # custom emoji -> name
    return re.sub(r"\s+", " ", text).strip(" *_-–—|")


def normalise_key(key):
    key = clean(key).lower().strip(" :")
    if key in ALIAS_TO_FIELD:
        return ALIAS_TO_FIELD[key]
    # Tolerate things like "Game System" / "System used" / "Sistema di gioco".
    for alias, field in sorted(ALIAS_TO_FIELD.items(), key=lambda kv: -len(kv[0])):
        if len(alias) > 2 and re.search(r"\b" + re.escape(alias) + r"\b", key):
            return field
    return None


def match_keyword(text, table):
    found = []
    for pattern, name in table:
        if re.search(pattern, text, re.IGNORECASE) and name not in found:
            found.append(name)
    return found


def normalise_value(field, value):
    """Map free-text system/language values onto canonical names."""
    if field == "system":
        hits = match_keyword(value, SYSTEM_KEYWORDS)
        # "D&D 5e" also matches the generic "D&D" rule: keep the most specific.
        if len(hits) > 1 and "D&D" in hits:
            hits.remove("D&D")
        return ", ".join(hits[:1]) if hits else value
    if field == "language":
        hits = match_keyword(value, LANGUAGE_KEYWORDS)
        return " / ".join(hits) if hits else value
    return value


# --------------------------------------------------------------------------
# Discord API
# --------------------------------------------------------------------------

class Discord:
    def __init__(self, token):
        if not token.lower().startswith(("bot ", "bearer ")):
            token = "Bot " + token
        self.headers = {
            "Authorization": token,
            "User-Agent": "DiscordBot (campaign-table, 1.0)",
        }

    def get(self, path, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        url = f"{API}{path}" + (f"?{query}" if query else "")
        for _ in range(5):
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry = json.load(e).get("retry_after", 1)
                    time.sleep(float(retry) + 0.1)
                    continue
                body = e.read().decode(errors="replace")
                raise SystemExit(f"Discord API error {e.code} on {path}: {body}")
        raise SystemExit(f"Rate-limited too many times on {path}")

    def channel_messages(self, channel_id, limit=None):
        messages, before = [], None
        while True:
            batch = self.get(f"/channels/{channel_id}/messages", limit=100, before=before)
            if not batch:
                break
            messages.extend(batch)
            before = batch[-1]["id"]
            if limit and len(messages) >= limit:
                return messages[:limit]
        return messages

    def forum_threads(self, channel):
        threads = {}
        active = self.get(f"/guilds/{channel['guild_id']}/threads/active")
        for t in active.get("threads", []):
            if t.get("parent_id") == channel["id"]:
                threads[t["id"]] = t
        before = None
        while True:
            page = self.get(f"/channels/{channel['id']}/threads/archived/public",
                            limit=100, before=before)
            for t in page.get("threads", []):
                threads[t["id"]] = t
            if not page.get("has_more") or not page.get("threads"):
                break
            before = page["threads"][-1]["thread_metadata"]["archive_timestamp"]
        return list(threads.values())

    def starter_message(self, thread_id):
        # In forum channels the starter message id equals the thread id.
        try:
            return self.get(f"/channels/{thread_id}/messages/{thread_id}")
        except SystemExit:
            msgs = self.get(f"/channels/{thread_id}/messages", limit=1, after=0)
            return msgs[0] if msgs else {}


def fetch(token, channel_id, limit=None):
    api = Discord(token)
    channel = api.get(f"/channels/{channel_id}")
    data = {"channel": channel, "posts": []}
    if channel.get("type") in (15, 16):  # GUILD_FORUM / GUILD_MEDIA
        tags = {t["id"]: t["name"] for t in channel.get("available_tags", [])}
        threads = api.forum_threads(channel)
        if limit:
            threads = threads[:limit]
        for i, thread in enumerate(threads, 1):
            print(f"\rFetching thread {i}/{len(threads)}", end="", file=sys.stderr)
            data["posts"].append({
                "id": thread["id"],
                "title": thread.get("name"),
                "tags": [tags.get(t, t) for t in thread.get("applied_tags", [])],
                "archived": thread.get("thread_metadata", {}).get("archived", False),
                "message": api.starter_message(thread["id"]),
            })
        print(file=sys.stderr)
    else:
        for msg in api.channel_messages(channel_id, limit):
            data["posts"].append({"id": msg["id"], "title": None, "tags": [],
                                  "archived": False, "message": msg})
    return data


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def message_text(msg):
    parts = [msg.get("content") or ""]
    for emb in msg.get("embeds", []):
        if emb.get("title"):
            parts.append(f"Title: {emb['title']}")
        if emb.get("author", {}).get("name"):
            parts.append(f"GM: {emb['author']['name']}")
        if emb.get("description"):
            parts.append(emb["description"])
        for f in emb.get("fields", []):
            parts.append(f"{f.get('name', '')}: {f.get('value', '')}")
        if emb.get("footer", {}).get("text"):
            parts.append(emb["footer"]["text"])
    return "\n".join(parts)


def parse_post(post, guild_id, channel_id):
    msg = post.get("message") or {}
    text = message_text(msg)
    fields = {}

    for line in re.split(r"\n|\s+[|•·]\s+", text):
        m = KV_LINE.match(line)
        if not m or URL.match(m.group("val")):
            continue
        field = normalise_key(m.group("key"))
        value = clean(m.group("val"))
        if field and value and field not in fields:
            fields[field] = normalise_value(field, value)

    # Forum tags: languages go to "language", everything else is a system/tag.
    other_tags = []
    for tag in post.get("tags", []):
        langs = match_keyword(tag, LANGUAGE_KEYWORDS)
        systems = match_keyword(tag, SYSTEM_KEYWORDS)
        if langs:
            fields.setdefault("language", " / ".join(langs))
        elif systems and "system" not in fields:
            fields["system"] = normalise_value("system", tag)
        else:
            other_tags.append(tag)

    # Fall back to keyword spotting over the whole post.
    haystack = " ".join(filter(None, [post.get("title"), text]))
    if "system" not in fields:
        hits = match_keyword(haystack, SYSTEM_KEYWORDS)
        if len(hits) > 1 and "D&D" in hits:
            hits.remove("D&D")
        if hits:
            fields["system"] = hits[0] + " (?)"
    if "language" not in fields:
        hits = match_keyword(haystack, LANGUAGE_KEYWORDS)
        if hits:
            fields["language"] = " / ".join(hits) + " (?)"

    if post.get("title"):
        fields.setdefault("title", post["title"])
    if "title" not in fields:
        first = next((clean(l) for l in text.splitlines() if clean(l)), "")
        fields["title"] = first[:80] + ("…" if len(first) > 80 else "")
    if "gm" not in fields and msg.get("author"):
        a = msg["author"]
        fields["gm"] = a.get("global_name") or a.get("username") or ""

    fields["tags"] = ", ".join(other_tags)
    fields["posted"] = (msg.get("timestamp") or "")[:10]
    fields["archived"] = "yes" if post.get("archived") else ""
    target = post["id"] if post.get("title") else f"{channel_id}/{post['id']}"
    fields["link"] = f"https://discord.com/channels/{guild_id}/{target}"
    return fields


def parse(data):
    ch = data["channel"]
    rows = [parse_post(p, ch.get("guild_id", "@me"), ch["id"]) for p in data["posts"]]
    return [r for r in rows if r.get("title") or r.get("system")]


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

BASE_COLUMNS = ["title", "system", "language", "gm", "schedule", "platform",
                "players", "level", "setting", "status", "cost", "tags",
                "posted", "archived", "link"]


def columns_for(rows, exclude=()):
    present = {k for r in rows for k, v in r.items() if v}
    return [c for c in BASE_COLUMNS if c in present and c not in exclude]


def group_value(row, field):
    """Value used for grouping: guessed "(?)" values join their certain peers."""
    return re.sub(r" \(\?\)$", "", row.get(field) or "") or UNKNOWN


def sort_key(row, group_by):
    return tuple(group_value(row, g).lower() for g in group_by) + (row.get("title", "").lower(),)


def md_escape(v):
    return str(v or "").replace("|", "\\|").replace("\n", " ")


def md_table(rows, cols):
    out = ["| " + " | ".join(c.capitalize() for c in cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c) or ""
            cells.append(f"[open]({v})" if c == "link" and v else md_escape(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def pivot(rows, a, b):
    counts = Counter((group_value(r, a), group_value(r, b)) for r in rows)
    rows_a = sorted({k[0] for k in counts}, key=str.lower)
    cols_b = sorted({k[1] for k in counts}, key=str.lower)
    return rows_a, cols_b, counts


def render_markdown(rows, group_by, channel_name):
    out = [f"# Campaigns in #{channel_name}", "",
           f"{len(rows)} campaigns. Values marked `(?)` were guessed from the post text.", ""]

    if len(group_by) >= 2:
        a, b = group_by[:2]
        ra, cb, counts = pivot(rows, a, b)
        out += [f"## Summary: {a} × {b}", "",
                f"| {a.capitalize()} | " + " | ".join(map(md_escape, cb)) + " | Total |",
                "|---|" + "---|" * (len(cb) + 1)]
        for x in ra:
            row = [counts.get((x, y), 0) for y in cb]
            out.append(f"| {md_escape(x)} | " + " | ".join(str(n or "") for n in row)
                       + f" | {sum(row)} |")
        out.append("")

    for g in group_by:
        c = Counter(group_value(r, g) for r in rows)
        out += [f"## By {g}", ""] + [f"- **{md_escape(k)}**: {n}" for k, n in
                                     sorted(c.items(), key=lambda kv: (-kv[1], kv[0].lower()))] + [""]

    out += ["## All campaigns", ""]
    rows = sorted(rows, key=lambda r: sort_key(r, group_by))
    groups = defaultdict(list)
    for r in rows:
        groups[group_value(r, group_by[0])].append(r)
    for top in sorted(groups, key=str.lower):
        out += [f"### {top}", "", md_table(groups[top], columns_for(groups[top], group_by[:1])), ""]
    return "\n".join(out)


def render_csv(rows, group_by):
    buf = io.StringIO()
    cols = columns_for(rows)
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: sort_key(r, group_by)))
    return buf.getvalue()


def render_html(rows, group_by, channel_name):
    cols = columns_for(rows)
    rows = sorted(rows, key=lambda r: sort_key(r, group_by))
    opts = {g: sorted({group_value(r, g) for r in rows}, key=str.lower) for g in group_by}
    e = html.escape
    filters = "".join(
        f'<label>{e(g.capitalize())} <select data-col="{e(g)}"><option value="">All</option>'
        + "".join(f"<option>{e(v)}</option>" for v in vals) + "</select></label>"
        for g, vals in opts.items())
    head = "".join(f'<th data-i="{i}">{e(c.capitalize())}</th>' for i, c in enumerate(cols))
    body = ""
    for r in rows:
        attrs = " ".join(f'data-{e(g)}="{e(group_value(r, g))}"' for g in group_by)
        cells = "".join(
            f'<td><a href="{e(r[c])}">open</a></td>' if c == "link" and r.get(c)
            else f"<td>{e(str(r.get(c) or ''))}</td>" for c in cols)
        body += f"<tr {attrs}>{cells}</tr>\n"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Campaigns</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#666;--line:#ddd;--head:#f4f4f6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16161a;--fg:#e8e8ea;--muted:#999;--line:#333;--head:#222228}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;margin:16px}}
.bar{{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}} input,select{{font:inherit}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:var(--head);cursor:pointer;position:sticky;top:0}} small{{color:var(--muted)}}
</style></head><body>
<h1>Campaigns in #{e(channel_name)}</h1>
<small><span id="n">{len(rows)}</span> of {len(rows)} campaigns · “(?)” = guessed from text · click a header to sort</small>
<div class="bar"><input id="q" placeholder="Search…">{filters}</div>
<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody id="tb">
{body}</tbody></table></div>
<script>
const tb=document.getElementById('tb'),sel=[...document.querySelectorAll('select')],q=document.getElementById('q');
function apply(){{let n=0;const s=q.value.toLowerCase();for(const tr of tb.rows){{
 const ok=sel.every(x=>!x.value||tr.dataset[x.dataset.col]===x.value)&&tr.textContent.toLowerCase().includes(s);
 tr.hidden=!ok;n+=ok}}document.getElementById('n').textContent=n}}
sel.forEach(x=>x.onchange=apply);q.oninput=apply;
document.querySelectorAll('th').forEach(th=>th.onclick=()=>{{const i=+th.dataset.i,d=th.dataset.d=th.dataset.d==='1'?'-1':'1';
 [...tb.rows].sort((a,b)=>a.cells[i].textContent.localeCompare(b.cells[i].textContent)*d).forEach(r=>tb.appendChild(r))}});
</script></body></html>
"""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel", default=DEFAULT_CHANNEL, help="channel id (default: %(default)s)")
    p.add_argument("--token", default=os.environ.get("DISCORD_TOKEN"),
                   help="bot token (default: $DISCORD_TOKEN)")
    p.add_argument("--format", choices=["markdown", "csv", "html", "json"], default="markdown")
    p.add_argument("-o", "--output", help="output file (default: stdout)")
    p.add_argument("--group-by", nargs="+", default=["system", "language"],
                   help="fields to group/summarise by (default: system language)")
    p.add_argument("--limit", type=int, help="only fetch the N most recent posts")
    p.add_argument("--include-archived", action="store_true",
                   help="keep archived (closed) forum threads")
    p.add_argument("--dump", help="save the raw fetched data to this JSON file")
    p.add_argument("--from-json", help="read raw data from a previous --dump instead of Discord")
    args = p.parse_args()

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            data = json.load(f)
    else:
        if not args.token:
            p.error("no token: set DISCORD_TOKEN or pass --token (see --help)")
        data = fetch(args.token, args.channel, args.limit)
        if args.dump:
            with open(args.dump, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)

    rows = parse(data)
    if not args.include_archived:
        rows = [r for r in rows if not r.get("archived")]
    name = data["channel"].get("name", args.channel)

    if args.format == "markdown":
        out = render_markdown(rows, args.group_by, name)
    elif args.format == "csv":
        out = render_csv(rows, args.group_by)
    elif args.format == "html":
        out = render_html(rows, args.group_by, name)
    else:
        out = json.dumps(sorted(rows, key=lambda r: sort_key(r, args.group_by)),
                         ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Wrote {len(rows)} campaigns to {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
