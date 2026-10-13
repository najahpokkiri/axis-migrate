import logging
log = logging.getLogger(__name__)


def run(ctx):
    try:
        ctx.read_vector("ds.zones")
    except Exception as e:
        log.exception(e)
        raise
