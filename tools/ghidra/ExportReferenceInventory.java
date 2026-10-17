// SPDX-License-Identifier: GPL-2.0-only
// Export local reference-program functions and call sites for completeness audits.
// Manufacturer-derived output must remain outside version control.
// @category OCTA-CAPTURE
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.address.Address;
import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

public class ExportReferenceInventory extends GhidraScript {
    private static String cell(String value) {
        return value.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ');
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) {
            throw new IllegalArgumentException("Expected local output directory");
        }
        Path output = Path.of(args[0]);
        Files.createDirectories(output);
        int functions = 0;
        int calls = 0;
        try (BufferedWriter writer = Files.newBufferedWriter(output.resolve("functions.tsv"), StandardCharsets.UTF_8)) {
            writer.write("address\tbytes\texternal\tthunk\tname\n");
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                monitor.checkCancelled();
                writer.write(function.getEntryPoint() + "\t" + function.getBody().getNumAddresses()
                    + "\t" + function.isExternal() + "\t" + function.isThunk() + "\t"
                    + cell(function.getName(true)) + "\n");
                functions++;
            }
        }
        try (BufferedWriter writer = Files.newBufferedWriter(output.resolve("calls.tsv"), StandardCharsets.UTF_8)) {
            writer.write("site\tcaller\ttarget\tname\n");
            for (Instruction instruction : currentProgram.getListing().getInstructions(true)) {
                monitor.checkCancelled();
                if (!instruction.getFlowType().isCall()) continue;
                Function caller = getFunctionContaining(instruction.getAddress());
                String from = caller == null ? "" : cell(caller.getName(true));
                Address[] targets = instruction.getFlows();
                if (targets.length == 0) {
                    writer.write(instruction.getAddress() + "\t" + from + "\tINDIRECT\t"
                        + cell(instruction.toString()) + "\n");
                }
                for (Address target : targets) {
                    Function callee = getFunctionAt(target);
                    writer.write(instruction.getAddress() + "\t" + from + "\t" + target + "\t"
                        + (callee == null ? "" : cell(callee.getName(true))) + "\n");
                }
                calls++;
            }
        }
        println("OCTA_INVENTORY_OK: " + functions + " functions; " + calls + " call sites");
    }
}
