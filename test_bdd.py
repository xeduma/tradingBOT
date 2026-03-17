import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv()
async def test():
    conn = await asyncpg.connect(
        f'postgresql://apex:{os.getenv(\"DB_PASSWORD\")}@localhost:5432/trading'
    )
    print('DB OK:', await conn.fetchval('SELECT version()'))
    await conn.close()
asyncio.run(test())
