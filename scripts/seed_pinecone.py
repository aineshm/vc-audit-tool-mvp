"""Seed the Pinecone comps index with public companies from EDGAR + yfinance.

Run once (or when you want to refresh the universe):

    python scripts/seed_pinecone.py [--sector enterprise_software] [--all-sectors]

The script:
  1. Fetches company universe from EDGAR (per SIC code)
  2. Fetches business descriptions + EV/Revenue from yfinance
  3. Upserts all valid companies into Pinecone as text records
     (Pinecone embeds them automatically via multilingual-e5-large)

After seeding, PineconeCompsRanker.rank() performs search-only — no more
upsert-on-every-call.

Environment variables required:
  PINECONE_API_KEY         — Pinecone API key
  PINECONE_INDEX_NAME      — index name (default: vc-audit-edgar-comps)
  PINECONE_EMBEDDING_MODEL — (default: multilingual-e5-large)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("seed_pinecone")

# Ensure the src package is importable when running from project root.
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))


def _upsert_batch(index: object, records: list[dict], namespace: str) -> None:
    """Upsert a batch of records with retry on transient errors."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            index.upsert_records(namespace=namespace, records=records)  # type: ignore[union-attr]
            return
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            wait = 2**attempt
            logger.warning("upsert failed (attempt %d/%d): %s — retrying in %ds", attempt + 1, max_retries, exc, wait)
            time.sleep(wait)


def seed(
    sectors: list[str] | None = None,
    batch_size: int = 50,
    dry_run: bool = False,
) -> None:
    from vc_audit_tool.data_sources.edgar_universe import SIC_SECTOR_MAP, EdgarCompanyUniverse
    from vc_audit_tool.data_sources.yfinance_metrics import YFinanceMetricsFetcher

    api_key = os.getenv("PINECONE_API_KEY", "")
    index_name = os.getenv("PINECONE_INDEX_NAME", "vc-audit-edgar-comps")
    embedding_model = os.getenv("PINECONE_EMBEDDING_MODEL", "multilingual-e5-large")

    if not api_key:
        logger.error("PINECONE_API_KEY not set — aborting")
        sys.exit(1)

    import pinecone

    pc = pinecone.Pinecone(api_key=api_key)

    # Create index if not present.
    if not pc.has_index(index_name):
        logger.info("Creating Pinecone index '%s' with model '%s'", index_name, embedding_model)
        pc.create_index_for_model(
            name=index_name,
            cloud="aws",
            region="us-east-1",
            embed={
                "model": embedding_model,
                "field_map": {"text": "description"},
            },
        )
        logger.info("Index created — waiting 10s for it to become ready…")
        time.sleep(10)
    else:
        logger.info("Index '%s' already exists — upserting records", index_name)

    index = pc.Index(index_name)

    # Resolve sectors to seed.
    all_sectors = sorted(set(SIC_SECTOR_MAP.values()))
    target_sectors = sectors if sectors else all_sectors
    logger.info("Seeding %d sector(s): %s", len(target_sectors), ", ".join(target_sectors))

    edgar = EdgarCompanyUniverse(cache_dir=Path("data/edgar_cache"))
    metrics_fetcher = YFinanceMetricsFetcher(cache_dir=Path("data/yfinance_metrics_cache"))

    # Build reverse map: sector → SIC codes
    sector_to_sic: dict[str, list[str]] = {}
    for sic, sector in SIC_SECTOR_MAP.items():
        sector_to_sic.setdefault(sector, []).append(sic)

    total_upserted = 0
    total_skipped = 0

    for sector in target_sectors:
        sic_codes = sector_to_sic.get(sector, [])
        if not sic_codes:
            logger.warning("No SIC codes for sector '%s' — skipping", sector)
            continue

        # Step 1: Build universe
        tickers: list[str] = []
        for sic in sic_codes:
            try:
                companies = edgar.list_by_sic(sic)
                tickers.extend(c.ticker for c in companies)
            except Exception as exc:
                logger.warning("EDGAR SIC %s: %s", sic, exc)

        tickers = list(dict.fromkeys(tickers))  # deduplicate preserving order
        if not tickers:
            logger.warning("No tickers found for sector '%s'", sector)
            continue

        logger.info("Sector '%s': %d tickers from EDGAR", sector, len(tickers))

        # Step 2: Fetch metrics + descriptions
        metrics_list = metrics_fetcher.fetch_many(tickers)
        valid = [m for m in metrics_list if m.has_valid_multiple and m.business_summary]

        skipped = len(metrics_list) - len(valid)
        total_skipped += skipped
        logger.info(
            "Sector '%s': %d valid (with description + EV/Revenue), %d skipped",
            sector, len(valid), skipped,
        )

        if not valid:
            continue

        # Step 3: Build and upsert records in batches
        records = [
            {
                "_id": m.ticker,
                "description": (
                    f"{m.company_name}. {m.business_summary}"
                    if m.business_summary
                    else m.company_name
                ),
                "ticker": m.ticker,
                "company_name": m.company_name,
                "sector": sector,
                "ev_to_revenue": str(float(m.ev_to_revenue)) if m.ev_to_revenue else "0",
            }
            for m in valid
        ]

        if dry_run:
            logger.info("[DRY RUN] Would upsert %d records for sector '%s'", len(records), sector)
            total_upserted += len(records)
            continue

        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            _upsert_batch(index, batch, namespace="comps")
            total_upserted += len(batch)
            logger.info(
                "Sector '%s': upserted batch %d-%d / %d",
                sector, i + 1, i + len(batch), len(records),
            )
            time.sleep(0.2)  # gentle rate limiting

    logger.info(
        "Seed complete: %d records upserted, %d skipped (missing data)",
        total_upserted, total_skipped,
    )


def main() -> None:
    all_sectors = sorted({
        "enterprise_software", "semiconductors", "technology_distribution",
        "investment_advice", "telecommunications", "defense_electronics",
        "ecommerce", "fintech", "healthcare_it", "biotech",
    })

    parser = argparse.ArgumentParser(description="Seed Pinecone comps index from EDGAR+yfinance")
    parser.add_argument(
        "--sector",
        nargs="+",
        choices=all_sectors,
        help="Sectors to seed (default: all)",
    )
    parser.add_argument(
        "--all-sectors",
        action="store_true",
        help="Seed all known sectors (default if no --sector given)",
    )
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be upserted without actually calling Pinecone",
    )
    args = parser.parse_args()

    sectors = args.sector if args.sector else None
    seed(sectors=sectors, batch_size=args.batch_size, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
