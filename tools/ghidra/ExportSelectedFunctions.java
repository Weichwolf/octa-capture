// SPDX-License-Identifier: GPL-2.0-only
// Export selected functions for local analysis. Keep proprietary output out of Git.
// @category OCTA-CAPTURE
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.io.BufferedWriter;

public class ExportSelectedFunctions extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) {
            throw new IllegalArgumentException("Expected targets.tsv and output directory");
        }
        Path output = Path.of(args[1]);
        Files.createDirectories(output);
        DecompInterface decompiler = new DecompInterface();
        int exported = 0;
        try (BufferedWriter index = Files.newBufferedWriter(output.resolve("index.tsv"), StandardCharsets.UTF_8)) {
            if (!decompiler.openProgram(currentProgram)) {
                throw new IllegalStateException("Unable to initialize decompiler");
            }
            for (String line : Files.readAllLines(Path.of(args[0]), StandardCharsets.UTF_8)) {
                monitor.checkCancelled();
                if (line.isBlank() || line.startsWith("#")) continue;
                String[] target = line.split("\t", 2);
                Address address = toAddr(target[0]);
                Function function = getFunctionAt(address);
                if (function == null) {
                    disassemble(address);
                    function = createFunction(address, null);
                }
                if (function == null) {
                    index.write(target[0] + "\tMISSING\t" + target[1] + "\n");
                    continue;
                }
                var result = decompiler.decompileFunction(function, 30, monitor);
                if (!result.decompileCompleted()) {
                    index.write(target[0] + "\tFAILED\t" + target[1] + "\t" + result.getErrorMessage() + "\n");
                    continue;
                }
                String text = "/* Local manufacturer reference; not project source. */\n"
                    + "/* " + target[0] + " " + target[1] + " */\n"
                    + result.getDecompiledFunction().getC();
                Files.writeString(output.resolve(target[0] + ".c"), text, StandardCharsets.UTF_8);
                index.write(target[0] + "\tOK\t" + target[1] + "\n");
                index.flush();
                exported++;
            }
        } finally {
            decompiler.dispose();
        }
        println("OCTA_EXPORT_OK: " + exported + " selected functions exported");
    }
}
