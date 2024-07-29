import logging
import asyncio
from amaranth import *
from amaranth.lib import io, cdc

from ... import *


class GamecubeHostSubtarget(Elaboratable):
    def __init__(self, ports, in_fifo, out_fifo, joybus_cyc):
        self.ports = ports
        self.in_fifo = in_fifo
        self.out_fifo = out_fifo
        self.joybus_cyc = joybus_cyc

    def elaborate(self, platform):
        m = Module()

        #data_in = Signal()
        #m.submodules += cdc.FFSynchronizer(self.data.i, data_in, init=1)

        m.submodules.data = data = io.Buffer(io.Direction.Bidir, self.ports.data)
        m.submodules.bit = p_bit = io.Buffer(io.Direction.Output, self.ports.bit)
        m.submodules.dbgval = p_dbgval = io.Buffer(io.Direction.Output, self.ports.dbgval)

        # poll_command = Const(0b0100_0000_0000_0011_0000_0010)
        # poll_command = cmd_shortpoll

        cmdbufwidth = 8
        cmd_buf = Signal(cmdbufwidth * 8)
        cmd_size_bytes = Signal(range(cmdbufwidth))

        position = Signal(range(cmd_buf.width + 1))  # plus stop bit plus one extra state for moving on :)
        # maybe have a flag but no

        usec_timer = Signal(range(self.joybus_cyc))
        m.d.sync += usec_timer.eq(usec_timer - 1)

        #usec_tick = Signal(32)
        #with m.If(usec_timer == 0):
            #m.d.sync += usec_tick.eq(usec_tick + 1)
            # usec_clock = Signal(1)
            # m.d.comb += usec_clock.eq(usec_timer == 0)

        countdown = Signal(range(4))
        countdown_en = Signal(1)
        m.d.comb += [
            p_dbgval.oe.eq(1),
            #p_dbgval.o.eq(countdown)
        ]

        response = Signal(64)
        response_pos = Signal(range(response.width + 1))


        BIT_ONE         = Const(0b0111, 4)
        BIT_ZERO        = Const(0b0001, 4)
        CONSOLE_STOP    = Const(0b011, 3)
        DEVICE_STOP     = Const(0b0011, 4)

        with m.If(usec_timer == 0):
            with m.If(countdown_en):
                m.d.sync += countdown.eq(countdown - 1)
            m.d.sync += usec_timer.eq(self.joybus_cyc)

        byte_pos = Signal(range(cmdbufwidth))
        bit_pos = Signal(range(8))

        m.d.comb += data.oe.eq(0)
        with m.FSM() as fsm:
            with m.State("READ"):
                m.d.comb += [
                    p_bit.oe.eq(1),
                    p_bit.o.eq(1)
                ]
                with m.If(self.out_fifo.r_rdy & (cmd_size_bytes < 7)):
                    m.d.comb += self.out_fifo.r_en.eq(1)
                    m.d.sync += [
                        cmd_buf.word_select(cmd_size_bytes, 8).eq(self.out_fifo.r_data),
                        cmd_size_bytes.eq(cmd_size_bytes + 1),
                    ]
                with m.Elif(cmd_size_bytes == (cmdbufwidth - 1)):
                    # buffer full
                    m.next = "SEND-COMMAND"
                with m.Elif((~self.out_fifo.r_rdy) & (cmd_size_bytes > 0)):
                    # done reading (~r_rdy)
                    m.next = "SEND-COMMAND"
            with m.State("SEND-COMMAND"):
                m.d.sync += byte_pos.eq(0)
                m.d.sync += bit_pos.eq(7)
                m.next = "SEND-NEXT-BIT"
            with m.State("SEND-NEXT-BIT"):
                m.d.comb += data.oe.eq(1)

                m.d.sync += usec_timer.eq(self.joybus_cyc)  # reset timer
                m.d.sync += countdown.eq(3)  # reset countdown

                cmd_size_bits = Signal(range(cmdbufwidth * 8))
                m.d.comb += cmd_size_bits.eq(cmd_size_bytes * 8)

                with m.If(byte_pos < cmd_size_bytes):
                    cur_byte = cmd_buf.word_select(byte_pos, 8)

                    bit = cur_byte.bit_select(bit_pos, 1)

                    with m.If(bit_pos != 0):
                        m.d.sync += bit_pos.eq(bit_pos - 1)
                    with m.Else():
                        m.d.sync += bit_pos.eq(7)
                        m.d.sync += byte_pos.eq(byte_pos + 1)

                    with m.If(bit == 0):
                        m.next = "SEND-0"
                    with m.Elif(bit == 1):
                        m.next = "SEND-1"
                with m.Else():
                    m.d.sync += position.eq(position + 1)
                    m.next = "SEND-STOP"

            with m.State("SEND-0"):
                m.d.comb += countdown_en.eq(1)
                m.d.comb += [
                    data.oe.eq(1),
                    data.o.eq(BIT_ZERO.bit_select(countdown, 1))
                ]

                with m.If((countdown == 0) & (usec_timer == 0)):
                    m.next = "SEND-NEXT-BIT"
            with m.State("SEND-1"):
                m.d.comb += countdown_en.eq(1)
                m.d.comb += [
                    data.oe.eq(1),
                    data.o.eq(BIT_ONE.bit_select(countdown, 1))
                ]

                with m.If((countdown == 0) & (usec_timer == 0)):
                    m.next = "SEND-NEXT-BIT"
            with m.State("SEND-STOP"):
                m.d.comb += countdown_en.eq(1)

                m.d.comb += data.oe.eq(1)

                with m.Switch(countdown):
                    with m.Case(3):
                        m.d.comb += data.o.eq(0)
                    with m.Case(2):
                        m.d.comb += data.o.eq(1)
                    with m.Case(1):
                        m.d.comb += data.o.eq(1)
                        # stop bit is 3 us long, so exit early
                        with m.If(usec_timer == 0):
                            m.d.sync += countdown.eq(countdown - 2)  # "skip" the 4th bit in this case
                            m.next = "DONE"
            with m.State("DONE"):
                m.d.sync += usec_timer.eq(self.joybus_cyc)  # reset timer
                m.d.sync += countdown.eq(3)  # reset countdown
                m.d.sync += position.eq(0)  # reset position

                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(0x55)
                ]
                m.next = "WAIT-FOR-CONTROLLER"
            with m.State("WAIT-FOR-CONTROLLER"):
                with m.If(data.i == 0):
                    m.next = "READ-BIT"
            with m.State("READ-BIT"):
                read_timer = Signal(range(4 * self.joybus_cyc))
                m.d.sync += read_timer.eq(read_timer + 1)

                duration_low = Signal(range(4 * self.joybus_cyc))

                saw_rising_edge = Signal()

                with m.If((data.i == 1) & (saw_rising_edge == 0)):
                    m.d.sync += duration_low.eq(read_timer)
                    m.d.sync += saw_rising_edge.eq(1)
                with m.Elif((data.i == 0) & (saw_rising_edge == 1)):
                    duration_high = (read_timer - duration_low)

                    thebit = duration_high > duration_low
                    m.d.sync += response.bit_select(response_pos, 1).eq(thebit)
                    m.d.sync += response_pos.eq(response_pos + 1)

                    m.d.sync += read_timer.eq(0)  # reset the timer before leaving
                    m.d.sync += saw_rising_edge.eq(0)

                    with m.If(response_pos < 64):
                        m.next = "READ-BIT"
                    with m.Else():
                        m.next = "YEET-BYTES"
                    # todo rely on controller stop bit, and do this one byte at a time.
            with m.State("YEET-BYTES"):
                counter = Signal(range(8))
                m.d.sync += counter.eq(counter + 1)

                m.d.comb += [
                    self.in_fifo.w_en.eq(1),
                    self.in_fifo.w_data.eq(response.word_select(counter, 8))
                ]
                with m.If(counter == 7):
                    m.d.sync += cmd_size_bytes.eq(0)
                    m.next = "READ"

        # debug
        m.d.comb += [
            p_dbgval.oe.eq(1),
            p_dbgval.o.eq(fsm.state)
        ]
        return m


class GamecubeHostInterface:
    def __init__(self, interface, logger):
        self._lower = interface
        self._logger = logger
        self._level = logging.DEBUG if self._logger.name == __name__ else logging.TRACE
        self._streaming = False

    def _log(self, message, *args):
        self._logger.log(self._level, "GC: " + message, *args)

    async def write(self, data):
        await self._lower.write(data)

    async def read(self, n):
        return await self._lower.read(n)

    async def stream(self, callback):
        await asyncio.sleep(3)
        print("amogus")
        #while True:
        #await self.write([0x40, 0x03, 0x02])  # shortpoll + 2 bytes params(??)
        await self.write([0x0])
        #await self.write([0x41])  # shortpoll + 2 bytes params(??)

        #cmd_shortpoll = Const(0x40)
        #cmd_readorigin = Const(0x41)

        await callback(await self.read(1))  # 0xff "confirmation byte" (can be removed once stuff works)

        await callback(await self.read(8))


class GamecubeHostApplet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "interface with Gamecube controllers"
    preview = True
    description = """
    gamecube
    """

    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)

        access.add_pin_argument(parser, "data", default=True)
        access.add_pin_argument(parser, "bit", default=True)
        access.add_pin_set_argument(parser, "dbgval", 4, default=True)


    def build(self, target, args):
        self.mux_interface = iface = target.multiplexer.claim_interface(self, args)

        joybus_cyc = self.derive_clock(clock_name="joybus", input_hz=target.sys_clk_freq, output_hz=1_000_000) + 1
        self.logger.info(f"{joybus_cyc} {target.sys_clk_freq}")
        return iface.add_subtarget(GamecubeHostSubtarget(
            ports=iface.get_port_group(
                data=args.pin_data,
                bit=args.pin_bit,
                dbgval=args.pin_set_dbgval,
            ),
            in_fifo=iface.get_in_fifo(),
            out_fifo=iface.get_out_fifo(),
            joybus_cyc=joybus_cyc,
        ))

    async def run(self, device, args):
        """

        @type device: glasgow.device.hardware.GlasgowHardwareDevice
        """
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args,
                                                           pull_high={args.pin_data})

        # if args.reset:
        #    await device.set_voltage(args.port_spec, 0.0)
        #    await asyncio.sleep(0.3)
        from glasgow.device.hardware import GlasgowHardwareDevice
        if device is GlasgowHardwareDevice:
            await device.set_voltage(args.port_spec, 3.3)

        return GamecubeHostInterface(iface, self.logger)

    @classmethod
    def add_run_arguments(cls, parser, access):
        super().add_run_arguments(parser, access)

    @classmethod
    def add_interact_arguments(cls, parser):
        pass

    async def interact(self, device, args, iface):
        async def print_gc_state(gc_packet):
            for byt in gc_packet:
                print(f"{byt:02x}", end=" ", flush=True)
            print()

        await iface.stream(print_gc_state)

    @classmethod
    def tests(cls):
        from . import test
        return test.GamecubeHostAppletTestCase
