"""元数据组件的组装：同步任务和管理员操作共用同一组匹配器、刮削器、补缺器和图片下载器。"""

from __future__ import annotations

from dataclasses import dataclass

from app.clock import Clock
from app.db import Database
from app.library.artwork import Artwork
from app.library.fill import Filler
from app.library.images import ImageStore
from app.library.matcher import Matcher
from app.library.metadata_providers import MetadataProviders
from app.library.scraper import Scraper


@dataclass(frozen=True)
class Metadata:
    providers: MetadataProviders
    scraper: Scraper
    filler: Filler
    matcher: Matcher
    artwork: Artwork

    @classmethod
    def create(
        cls, database: Database, clock: Clock, providers: MetadataProviders, images: ImageStore
    ) -> Metadata:
        filler = Filler(database, clock, providers)
        scraper = Scraper(database, clock, providers, filler)
        return cls(
            providers=providers,
            scraper=scraper,
            filler=filler,
            matcher=Matcher(database, clock, providers, scraper),
            artwork=Artwork(database, providers, images),
        )
