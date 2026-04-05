import asyncio
import sys

from app.database import AsyncSessionLocal, init_db
from app.services.video_processor import VideoProcessor


async def main(bv_id: str) -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        result = await VideoProcessor().process_video(session, bv_id)
        print(result)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: python test_single_video.py <BV号>")
    asyncio.run(main(sys.argv[1]))
