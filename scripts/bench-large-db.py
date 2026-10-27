"""大数据量查询基准：用合成数据灌库，经真实服务代码路径测量首页、片库、搜索、WebDAV 目录树等。

用法（在 backend/ 下）：
    uv run python ../scripts/bench-large-db.py [--movies 8000 --series 1500 ...]

数据库建在临时目录，跑完即删；不联网、不碰真实服务。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

# 允许从 backend/ 以外的目录运行：把 backend 加进 import 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.api.webdav import _nodes  # noqa: E402
from app.auth.users import User  # noqa: E402
from app.clock import SystemClock, to_iso  # noqa: E402
from app.db import Database  # noqa: E402
from app.library.browse import BrowseQueries  # noqa: E402

SLOW_MS = 250  # backend/app/http_middleware.py 的慢请求阈值
CHARS = (
    "星河夜空海风云山林雨雪月光影梦城岛湖火龙虎鹰狼狐鹿花草木石金银铁玉雷电冰霜春夏秋冬晨暮"
    "红蓝青绿黑白紫金长短高远深浅古今东西南北天地人心剑刀弓王帝侠客旅途故事传说奇迹迷雾荒野"
    "归来重逢告别时代年华岁月少年青春爱情家园战争和平命运黎明黄昏边境港湾沙漠森林峡谷"
)
GENRES = ["动作", "喜剧", "剧情", "科幻", "悬疑", "爱情", "动画", "纪录", "恐怖", "奇幻", "犯罪", "战争"]
SYLL = ["an", "bo", "chen", "da", "el", "fan", "gu", "han", "jin", "ke", "li", "mo", "na", "pei"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--libraries", type=int, default=5, help="资料库数（约 60%% 电影库，其余剧集库）")
    p.add_argument("--movies", type=int, default=8000)
    p.add_argument("--series", type=int, default=1500)
    p.add_argument("--seasons", type=int, default=3, help="每剧季数")
    p.add_argument("--episodes", type=int, default=10, help="每季集数")
    p.add_argument("--people", type=int, default=30000)
    p.add_argument("--credits", type=int, default=120000)
    p.add_argument("--repeat", type=int, default=5)
    p.add_argument("--seed", type=int, default=20260101)
    p.add_argument("--analyze", action="store_true", help="灌库后执行 ANALYZE（默认不执行，贴近未跑过 optimize 的实例）")
    return p.parse_args()


# ---------------------------------------------------------------- 合成数据


class Gen:
    def __init__(self, args: argparse.Namespace) -> None:
        self.a = args
        self.rnd = random.Random(args.seed)
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.titles: list[str] = []

    def word(self) -> str:
        return "".join(self.rnd.choices(CHARS, k=2))

    def title(self) -> str:
        return "".join(self.word() for _ in range(self.rnd.choice((1, 2, 2, 3))))

    def ts(self, days: int = 1095) -> str:
        return to_iso(self.now - timedelta(seconds=self.rnd.randrange(days * 86400)))

    def person_name(self) -> str:
        return "".join(self.rnd.choices(CHARS, k=self.rnd.choice((2, 3, 3))))

    def latin(self) -> str:
        return " ".join(
            "".join(self.rnd.choices(SYLL, k=2)).capitalize() for _ in range(2)
        )


def load(db: Database, a: argparse.Namespace) -> dict[str, object]:
    g = Gen(a)
    rnd = g.rnd
    now = to_iso(g.now)
    n_lib = a.libraries
    n_movie_lib = max(1, round(n_lib * 0.6))
    n_series_lib = n_lib - n_movie_lib
    if n_series_lib < 1:
        raise SystemExit("--libraries 至少 2")
    lib_kind = ["movie"] * n_movie_lib + ["series"] * n_series_lib  # 库 id = 下标 + 1
    n_media = a.movies + a.series
    per_series_eps = a.seasons * a.episodes
    n_ep = a.series * per_series_eps
    n_play = a.movies + n_ep  # playable id: 电影 = media id；单集 = a.movies + episode id
    conn = db.connect()
    conn.execute("PRAGMA foreign_keys = OFF")  # 批量灌入；结束后用 foreign_key_check 校验
    conn.execute("BEGIN")
    ins = conn.executemany

    ins("INSERT INTO user(id,username,password_hash,role,token_version,created_at,updated_at,all_libraries)"
        " VALUES (?,?,?,?,1,?,?,0)",
        [(1, "admin", "x", "admin", now, now), (2, "viewer", "x", "user", now, now)])
    ins("INSERT INTO library(id,name,kind,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        [(i + 1, f"资料库{i + 1}", k, i, now, now) for i, k in enumerate(lib_kind)])
    conn.execute("INSERT INTO library_access(user_id,library_id) VALUES (2,1)")
    conn.execute("INSERT INTO plugin(id,name,version,updated_at) VALUES ('bench','bench','1',?)", (now,))
    conn.execute("INSERT INTO resource_source(id,plugin_id,name,created_at,updated_at)"
                 " VALUES (1,'bench','bench',?,?)", (now, now))
    ins("INSERT INTO resource_root(id,source_id,locator,display_path,created_at) VALUES (?,1,?,?,?)",
        [(i + 1, f"r{i}", f"/r{i}", now) for i in range(n_lib)])
    ins("INSERT INTO library_folder(id,library_id,cid,cid_path,path,created_at,resource_root_id)"
        " VALUES (?,?,?,?,?,?,?)",
        [(i + 1, i + 1, f"cid{i}", f"/{i}", f"/{i}", now, i + 1) for i in range(n_lib)])

    # media_item：电影在前（id 1..movies），剧集在后；电影分到电影库，剧集分到剧集库
    media: list[tuple[int, int, str, str, int | None]] = []  # id, library, kind, title, year
    rows = []
    images = []
    for mid in range(1, n_media + 1):
        is_movie = mid <= a.movies
        kind = "movie" if is_movie else "series"
        lib = rnd.randrange(n_movie_lib) + 1 if is_movie else n_movie_lib + rnd.randrange(n_series_lib) + 1
        title = g.title()
        g.titles.append(title)
        year = rnd.randint(1960, 2025) if rnd.random() < 0.97 else None
        poster = mid if rnd.random() < 0.85 else None
        backdrop = n_media + mid if rnd.random() < 0.5 else None
        if poster:
            images.append((poster, f"p{mid:08x}", "poster", f"bench://p/{mid}", now))
        if backdrop:
            images.append((backdrop, f"b{mid:08x}", "backdrop", f"bench://b/{mid}", now))
        media.append((mid, lib, kind, title, year))
        rows.append((
            mid, lib, kind, "bench", str(mid), title,
            g.word() + " " + g.latin() if rnd.random() < 0.6 else None,
            g.latin() if rnd.random() < 0.5 else None, year,
            f"{year}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}" if year else None,
            "概述 " + "".join(rnd.choices(CHARS, k=120)),
            round(rnd.uniform(3, 9.5), 1) if rnd.random() < 0.9 else None,
            json.dumps(rnd.sample(GENRES, rnd.randint(1, 3)), ensure_ascii=False),
            rnd.randint(40, 180), poster, backdrop, now, g.ts(), title,
        ))
    ins("INSERT INTO image(id,hash,kind,source,created_at) VALUES (?,?,?,?,?)", images)
    ins("INSERT INTO media_item(id,library_id,kind,provider_id,remote_id,title,original_title,"
        "en_title,year,release_date,overview,rating,genres,runtime,poster_image_id,"
        "backdrop_image_id,scraped_at,added_at,sort_title) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)

    # season / episode / playable
    seasons, episodes, playables = [], [], []
    sid = eid = 0
    series_eps: dict[int, list[tuple[int, int, int, int]]] = {}  # media -> (ep_id, season, ep_no, playable)
    for mid in range(1, a.movies + 1):
        playables.append((mid, mid, None, now))
    for mid in range(a.movies + 1, n_media + 1):
        lst = series_eps.setdefault(mid, [])
        for sn in range(1, a.seasons + 1):
            sid += 1
            seasons.append((sid, mid, sn, f"第{sn}季"))
            for en in range(1, a.episodes + 1):
                eid += 1
                pid = a.movies + eid
                episodes.append((eid, sid, en, f"第{en}集 {g.word()}", rnd.randint(20, 60)))
                playables.append((pid, mid, eid, now))
                lst.append((eid, sn, en, pid))
    ins("INSERT INTO season(id,media_item_id,number,name) VALUES (?,?,?,?)", seasons)
    ins("INSERT INTO episode(id,season_id,number,name,runtime) VALUES (?,?,?,?,?)", episodes)
    ins("INSERT INTO playable(id,media_item_id,episode_id,created_at) VALUES (?,?,?,?)", playables)

    # work（每个作品一个，matched） / cloud_file / version / subtitle / nfo
    works, files, versions, subs = [], [], [], []
    ndir: dict[int, str] = {}
    seen_dirs: set[tuple[int, str]] = set()
    cid = n_play  # cloud_file id：视频 = playable id；其后是 NFO、字幕
    for mid, lib, kind, title, year in media:
        d = f"{title} ({year})" if year else title
        if (lib, d) in seen_dirs:  # work 的 (folder, path) 唯一；重名时加编号（和真实库里重名目录同理）
            d = f"{d} {mid}"
        seen_dirs.add((lib, d))
        ndir[mid] = d
        works.append((mid, lib, d, title, year, mid, now))
        if kind == "movie":
            vids = [(mid, f"{d}/{d}.mkv", mid)]
        else:
            vids = [(pid, f"{d}/Season {sn:02d}/{title} S{sn:02d}E{en:02d}.mkv", pid)
                    for _, sn, en, pid in series_eps[mid]]
        for fid, rel, pid in vids:
            state = "missing" if rnd.random() < 0.01 else "active"
            files.append((fid, lib, rel, "video", state, f"f{fid}", rnd.randint(10**8, 10**10), mid, now))
            versions.append((fid, pid, fid, "1080p", now))
        cid += 1
        files.append((cid, lib, f"{d}/{'tvshow' if kind == 'series' else 'movie'}.nfo", "nfo",
                      "active", f"f{cid}", 2000, mid, now))
        if kind == "movie" and rnd.random() < 0.3:
            cid += 1
            files.append((cid, lib, f"{d}/{d}.zh.srt", "subtitle", "active", f"f{cid}", 5000, mid, now))
            subs.append((cid, mid, cid, "zh", "中文"))
    ins("INSERT INTO work(id,folder_id,path,title,year,state,media_item_id,created_at,updated_at)"
        " VALUES (?,?,?,?,?,'matched',?,?,?)", [(w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[6]) for w in works])
    ins("INSERT INTO cloud_file(id,folder_id,rel_path,kind,state,file_id,size,work_id,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)", [(f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8], f[8]) for f in files])
    ins("INSERT INTO version(id,playable_id,cloud_file_id,label,created_at) VALUES (?,?,?,?,?)", versions)
    ins("INSERT INTO subtitle(id,version_id,cloud_file_id,language,label) VALUES (?,?,?,?,?)",
        [(s[0], s[1], s[2], s[3], s[4]) for s in subs])

    # person / credit
    people = []
    for pid in range(1, a.people + 1):
        people.append((pid, "bench", str(pid), g.person_name(), g.latin() if rnd.random() < 0.4 else None))
    ins("INSERT INTO person(id,provider_id,remote_id,name,original_name) VALUES (?,?,?,?,?)", people)
    credits: Iterator[tuple[int, int, str, str | None, int]] = (
        (rnd.randint(1, n_media), rnd.randint(1, a.people),
         "director" if rnd.random() < 0.1 else "cast", g.word() if rnd.random() < 0.7 else None, i)
        for i in range(a.credits)
    )
    ins("INSERT INTO credit(media_item_id,person_id,role,character,sort_order) VALUES (?,?,?,?,?)", credits)

    # 播放进度：管理员随机 300 条；普通用户从其可见库 1 的电影里取 100 条
    movies_lib1 = [m[0] for m in media if m[0] <= a.movies and m[1] == 1]
    states = [(1, p, rnd.randint(1, 5000), 6000, int(rnd.random() < 0.5), g.ts(60), now)
              for p in rnd.sample(range(1, n_play + 1), min(300, n_play))]
    states += [(2, p, 100, 6000, int(rnd.random() < 0.5), g.ts(60), now)
               for p in rnd.sample(movies_lib1, min(100, len(movies_lib1)))]
    ins("INSERT INTO playback_state(user_id,playable_id,position,duration,watched,last_played_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?)", states)
    conn.execute("COMMIT")
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        raise SystemExit(f"合成数据外键不完整（脚本 bug）：{[tuple(r) for r in broken[:5]]}")
    if a.analyze:
        conn.execute("ANALYZE")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("library", "media_item", "season", "episode", "playable", "cloud_file",
                  "version", "person", "credit", "playback_state")
    }
    conn.close()

    # 用于搜索的词：按出现的汉字频次取热门字，再取一个具体片名和一个人名
    freq = Counter(ch for t in g.titles for ch in set(t))
    common_char = freq.most_common(1)[0][0]
    return {
        "counts": counts,
        "common_char": common_char,
        "common_word": next(t[:2] for t in g.titles if t[0] == common_char and len(t) >= 2),
        "full_title": g.titles[rnd.randrange(len(g.titles))],
        "person": people[rnd.randrange(len(people))][3],
        "top_person": None,
        "movie_id": 1,
        "series_id": a.movies + 1,
    }


# ---------------------------------------------------------------- 测量


def mk_user(uid: int, name: str, role: str) -> User:
    return User(uid, name, role, False, 1, "2026-01-01T00:00:00+00:00")  # type: ignore[arg-type]


QUERY_TIMEOUT_S = 60


def _arm_timeout(conn: sqlite3.Connection) -> None:
    """单次测量最多 60 秒，超时由 SQLite 中断（抛 OperationalError: interrupted），避免病态查询卡死基准。"""
    deadline = time.perf_counter() + QUERY_TIMEOUT_S
    conn.set_progress_handler(lambda: int(time.perf_counter() > deadline), 100_000)


def timed(db: Database, user: User, fn: Callable[[BrowseQueries, object], object], repeat: int) -> tuple[float, float]:
    """每次都开一个新读会话 + 新建 BrowseQueries，贴近一次 HTTP 请求。"""
    ms = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        with db.read_session() as conn:
            _arm_timeout(conn)
            fn(BrowseQueries(conn, user), conn)
        ms.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ms), max(ms)


def timed_webdav(db: Database, user: User, repeat: int) -> tuple[float, float, int]:
    ms, size = [], 0
    for _ in range(repeat):
        t0 = time.perf_counter()
        with db.read_session() as conn:
            _arm_timeout(conn)
            size = len(_nodes(conn, user))
        ms.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ms), max(ms), size


def explain(db: Database, user: User, term: str) -> list[tuple[str, list[str], bool]]:
    """抓取 search_all 实际执行的 SQL，对每条 SELECT 跑 EXPLAIN QUERY PLAN。"""
    captured: list[str] = []
    with db.read_session() as conn:
        conn.set_trace_callback(captured.append)
        BrowseQueries(conn, user).search_all(term, 0, 30)
        conn.set_trace_callback(None)
        out = []
        for sql in captured:
            if not re.match(r"\s*(SELECT|WITH)", sql, re.I):
                continue
            plan = [r["detail"] for r in conn.execute("EXPLAIN QUERY PLAN " + sql)]
            # 只统计「顶层」对大表的全表扫描：SCAN <表> 且不带 USING INDEX
            # 不含 CO-ROUTINE 内部步骤和 library 小表；LIKE '%x%' 不能用索引，表现为
            # 「SEARCH m USING INDEX ...(library_id=?)」即按库范围遍历该库全部条目，不是 SCAN 字样
            scans = [d for d in plan if d.startswith("SCAN") and "USING" not in d]
            first = " ".join(sql.split())[:70]
            out.append((first, plan, bool(scans)))
        return out


def table(title: str, rows: list[tuple[str, float, float, str]]) -> None:
    w = max(len(r[0]) for r in rows)
    print(f"\n### {title}\n")
    print(f"{'查询'.ljust(w)} | 中位(ms) | 最大(ms) | 备注")
    print(f"{'-' * w}-+----------+----------+------")
    for name, med, mx, note in rows:
        flag = " >250" if mx > SLOW_MS else ""
        if mx != mx:  # NaN：没有测出结果
            print(f"{name.ljust(w)} |      n/a |      n/a | {note}")
            continue
        print(f"{name.ljust(w)} | {med:8.1f} | {mx:8.1f} | {note}{flag}")


def main() -> None:
    a = parse_args()
    with tempfile.TemporaryDirectory(prefix="iris-bench-") as tmp:
        db = Database(Path(tmp) / "iris.db")
        t0 = time.perf_counter()
        version = db.migrate(SystemClock())
        t_migrate = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        info = load(db, a)
        t_load = time.perf_counter() - t0
        size_mb = sum(f.stat().st_size for f in Path(tmp).glob("iris.db*")) / 1e6
        print(f"建库迁移：{t_migrate:.1f} ms（schema 版本 {version}）")
        print(f"灌数据：{t_load:.1f} s；数据库文件：{size_mb:.1f} MB；analyze={a.analyze}")
        print("行数：" + ", ".join(f"{k}={v}" for k, v in info["counts"].items()))  # type: ignore[union-attr]

        admin, viewer = mk_user(1, "admin", "admin"), mk_user(2, "viewer", "user")
        cc, cw, ft, pn = info["common_char"], info["common_word"], info["full_title"], info["person"]
        with db.read_session() as c:
            top_person = c.execute(
                "SELECT person_id FROM credit GROUP BY person_id ORDER BY COUNT(*) DESC LIMIT 1"
            ).fetchone()[0]
        n = a.repeat

        cases: list[tuple[str, Callable[[BrowseQueries, object], object], str]] = [
            ("首页 home()", lambda q, c: q.home(), ""),
            ("资料库列表 libraries()", lambda q, c: q.libraries(), ""),
            ("片库详情(类型/年份) library(1)", lambda q, c: q.library(1), "DISTINCT json_each"),
            ("片库 按添加时间 p1", lambda q, c: q.library_items(1, sort="added", order=None, genre=None, year=None, offset=0, limit=50), ""),
            ("片库 按名称 p1", lambda q, c: q.library_items(1, sort="name", order=None, genre=None, year=None, offset=0, limit=50), ""),
            ("片库 按评分 p1", lambda q, c: q.library_items(1, sort="rating", order=None, genre=None, year=None, offset=0, limit=50), "无评分索引"),
            ("片库 按上映日期 p1", lambda q, c: q.library_items(1, sort="release", order=None, genre=None, year=None, offset=0, limit=50), "无上映日期索引"),
            ("片库 按名称 深翻页 offset=2000", lambda q, c: q.library_items(1, sort="name", order=None, genre=None, year=None, offset=2000, limit=50), ""),
            ("片库 类型筛选", lambda q, c: q.library_items(1, sort="added", order=None, genre="科幻", year=None, offset=0, limit=50), ""),
            ("片库 未看筛选", lambda q, c: q.library_items(1, sort="added", order=None, genre=None, year=None, watched=False, offset=0, limit=50), ""),
            ("片库 重复版本筛选", lambda q, c: q.library_items(1, sort="added", order=None, genre=None, year=None, status="multiple_versions", offset=0, limit=50), ""),
            ("条目详情 电影", lambda q, c: q.item(info["movie_id"]), ""),  # type: ignore[arg-type]
            ("条目详情 剧集", lambda q, c: q.item(info["series_id"]), ""),  # type: ignore[arg-type]
            ("人物详情(作品最多者)", lambda q, c: q.person(top_person), ""),
            (f"搜索 热门单字「{cc}」", lambda q, c: q.search_all(cc, 0, 30), "多命中"),
            (f"搜索 热门词「{cw}」", lambda q, c: q.search_all(cw, 0, 30), ""),
            (f"搜索 完整片名「{ft}」", lambda q, c: q.search_all(ft, 0, 30), ""),
            (f"搜索 人名「{pn}」", lambda q, c: q.search_all(pn, 0, 30), ""),
            ("搜索 无命中「zzqq」", lambda q, c: q.search_all("zzqq", 0, 30), "必须扫完"),
        ]
        for who, user in (("管理员（5 库全可见）", admin), ("普通用户（仅分配库 1）", viewer)):
            rows = []
            for name, fn, note in cases:
                try:
                    print(f"  [{who[:3]}] {name}", file=sys.stderr, flush=True)
                    med, mx = timed(db, user, fn, n)
                    rows.append((name, med, mx, note))
                except sqlite3.OperationalError as exc:
                    note = f"超过 {QUERY_TIMEOUT_S}s 被中断" if "interrupted" in str(exc) else f"失败: {exc}"
                    rows.append((name, float("nan"), float("nan"), note))
                except Exception as exc:  # 记录失败而不是中断整个基准（如普通用户看不到剧集库里的条目）
                    rows.append((name, float("nan"), float("nan"), f"失败: {type(exc).__name__}: {exc}"))
            med, mx, size = timed_webdav(db, user, n)
            rows.append(("WebDAV _nodes() 目录树(缓存未命中)", med, mx, f"{size} 个节点"))
            table(who, rows)

        print("\n### 搜索 EXPLAIN QUERY PLAN（管理员，search_all 实际执行的 SQL）")
        for first, plan, scan in explain(db, admin, cw):
            print(f"\n- {first}...  含无索引 SCAN 步骤: {'是' if scan else '否'}")
            for line in plan:
                print(f"    {line}")


if __name__ == "__main__":
    main()
