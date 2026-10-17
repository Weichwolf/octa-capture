// SPDX-License-Identifier: GPL-2.0-only
// Local reference analysis only; no native execution and no hardware access.
import ghidra.app.script.GhidraScript;
import ghidra.app.emulator.EmulatorHelper;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;

public class EmulatePresetWriter extends GhidraScript {
    @Override public void run() throws Exception {
        if (!"634c140bc4e49b0d65c70d5156be19573ad3d440ac5ea24e24d3ad59974da406"
                .equals(currentProgram.getExecutableSHA256())) {
            throw new IllegalArgumentException("Expected the Windows 1.5.3 RDDP1110.EXE reference");
        }
        if (getScriptArgs().length != 1) {
            throw new IllegalArgumentException("Expected an output directory");
        }
        Path directory = Path.of(getScriptArgs()[0]);
        Files.createDirectories(directory);
        final long state = 0x71000000L;
        final long file = 0x72000000L;
        final long vtable = 0x73000000L;
        final long write = 0x74000000L;
        final long stop = 0x75000000L;
        final long stack = 0x7600fff8L;
        for (int pattern = 0; pattern < 2; ++pattern) {
            EmulatorHelper emu = new EmulatorHelper(currentProgram);
            try {
                for (String register : new String[]{"RAX", "RBX", "RCX", "RDX", "RSI", "RDI", "RBP",
                        "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15", "RSP"}) {
                    emu.writeRegister(register, 0);
                }
                byte[] data = new byte[0x4000];
                if (pattern != 0) for (int i = 0; i < data.length; ++i) data[i] = (byte)(i * 37 + 11);
                emu.writeMemory(toAddr(state), data);
                emu.writeMemory(toAddr(file), new byte[0x100]);
                emu.writeMemory(toAddr(vtable), new byte[0x100]);
                emu.writeMemory(toAddr(0x76000000L), new byte[0x10000]);
                emu.writeMemoryValue(toAddr(file), 8, vtable);
                emu.writeMemoryValue(toAddr(file + 0x28), 4, 1);
                emu.writeMemoryValue(toAddr(vtable + 0x78), 8, write);
                emu.writeMemoryValue(toAddr(stack), 8, stop);
                emu.writeRegister("RCX", file);
                emu.writeRegister("RDX", state);
                emu.writeRegister("RSP", stack);
                emu.setBreakpoint(toAddr(write));
                emu.setBreakpoint(toAddr(stop));
                ByteArrayOutputStream output = new ByteArrayOutputStream();
                // The parent Save function writes these two NUL-terminated strings.
                output.write("OCTA-CAPTURE Control Panel Data\0Ver100\0".getBytes(StandardCharsets.US_ASCII));
                boolean running = emu.run(toAddr(0x140007820L), null, monitor);
                int writes = 0;
                while (running && emu.getExecutionAddress().getOffset() != stop) {
                    monitor.checkCancelled();
                    if (++writes > 4096 || emu.getExecutionAddress().getOffset() != write)
                        throw new IllegalStateException("Unexpected emulation stop: " + emu.getExecutionAddress());
                    long address = emu.readRegister("RDX").longValue();
                    int size = emu.readRegister("R8").intValue();
                    if (size != 1 && size != 4) throw new IllegalStateException("Unexpected write size: " + size);
                    output.write(emu.readMemory(toAddr(address), size));
                    long rsp = emu.readRegister("RSP").longValue();
                    long returned = ByteBuffer.wrap(emu.readMemory(toAddr(rsp), 8)).order(ByteOrder.LITTLE_ENDIAN).getLong();
                    emu.writeRegister("RSP", rsp + 8);
                    emu.writeRegister("RAX", 0);
                    emu.getEmulator().setExecuteAddress(returned);
                    running = emu.run(monitor);
                }
                if (!running || emu.readRegister("RAX").longValue() != 1)
                    throw new IllegalStateException("Writer failed: " + emu.getLastError());
                if (output.size() != 1639 || writes != 592) {
                    throw new IllegalStateException("Unexpected preset layout");
                }
                Path result = directory.resolve("preset-ver100-windows-" + pattern + ".octa");
                Files.write(result, output.toByteArray());
                println("PRESET_REFERENCE_OK pattern=" + pattern + " bytes=" + output.size() + " writes=" + writes);
            } finally { emu.dispose(); }
        }
    }
}
