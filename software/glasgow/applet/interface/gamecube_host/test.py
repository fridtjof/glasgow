from pprint import pprint

from amaranth import Module

from ... import *
from . import GamecubeHostApplet


class GamecubeHostAppletTestCase(GlasgowAppletTestCase, applet=GamecubeHostApplet):
    @synthesis_test
    def test_build(self):
        self.assertBuilds()

    def setup_loopback(self):
        self.build_simulated_applet()

    @applet_simulation_test("setup_loopback")
    async def test_loopback(self):
        iface = await self.run_simulated_applet()
        await iface.write(bytes([0xFF]))
        # poll runs now
        #self.assertEqual(True, False)
        respo = await iface.read(1)
        pprint(respo)
        #self.assertNotEqual(respo, bytes([0, 0, 0, 0, 0, 0, 0, 0]))
        self.assertEqual(respo, bytes([0]))
