import sys
import tempfile
import unittest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_schema_without_tv_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            params = StdioServerParameters(
                command=sys.executable, args=['-m', 'server'],
                env={'TV_HOST': '192.168.1.100', 'TV_STATE_DIR': folder})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    names = {tool.name for tool in tools}
                    self.assertEqual(len(names), 17)
                    self.assertTrue({'tv_power', 'tv_ui_run', 'tv_select', 'tv_learn'} <= names)
                    result = await session.call_tool('tv_devices', {})
                    self.assertFalse(result.isError)
                    self.assertIn('"devices": []', result.content[0].text)
