-- 合集的名称和图片：自动合集用 TMDB 系列的名称和海报，管理员可以指定一个成员的海报作封面。
-- tmdb_collection 缓存抓取条目时顺带拿到的系列信息（TMDB 的 ID 全局唯一，不分资料库）。
CREATE TABLE tmdb_collection (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    poster_path TEXT,
    backdrop_path TEXT
);

ALTER TABLE collection ADD COLUMN poster_path TEXT;

ALTER TABLE collection ADD COLUMN poster_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL;

ALTER TABLE collection ADD COLUMN backdrop_path TEXT;

ALTER TABLE collection ADD COLUMN backdrop_image_id INTEGER REFERENCES image (id) ON DELETE SET NULL;

ALTER TABLE collection ADD COLUMN cover_item_id INTEGER REFERENCES media_item (id) ON DELETE SET NULL;
