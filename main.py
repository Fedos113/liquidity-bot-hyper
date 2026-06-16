import argparse
import logging
import sys


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("liqbot.log"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="WETH/USDC Concentrated Liquidity Bot (Base/Aerodrome)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Override DRY_RUN from .env",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL from .env",
    )

    args = parser.parse_args()

    from src.config import config

    if args.log_level:
        config.LOG_LEVEL = args.log_level
    if args.dry_run is not None:
        config.DRY_RUN = args.dry_run

    setup_logging(config.LOG_LEVEL)
    logger = logging.getLogger("liqbot")
    logger.info("=== WETH/USDC Liquidity Bot (Base) ===")

    try:
        from src.bot import run_bot
        run_bot()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
