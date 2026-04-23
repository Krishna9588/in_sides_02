import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    # Connect to MCP YouTube server
    server_params = StdioServerParameters(
        command="mcp-youtube",
        args=[],
        env=None
    )
    
    async with stdio_client(server_params) as (stdio, write):
        async with ClientSession(stdio, write) as session:
            await session.initialize()
            
            # Call download_subtitles tool
            result = await session.call_tool(
                "download_subtitles",
                {
                    "url": "https://www.youtube.com/watch?v=0N86U8W7A4c",
                    "language": "en",
                    "format": "srt"
                }
            )
            print("Result:", result)

if __name__ == "__main__":
    asyncio.run(main())

# path = client.download_video(
#     url="https://youtu.be/dQw4w9WgXcQ",
#     quality="best",
#     format="mp4",
#     resolution="1080p",
# )

# path2 = client.download_subtitles(
#     url="https://www.youtube.com/watch?v=0N86U8W7A4c",
#     language="en",
#     format="srt",
# )

# print("Saved to", path2)