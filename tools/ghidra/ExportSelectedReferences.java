// SPDX-License-Identifier: GPL-2.0-only
// Export analyzed references to selected local manufacturer address ranges.
// Manufacturer-derived output must remain outside version control.
// @category OCTA-CAPTURE
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.symbol.Reference;
import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

public class ExportSelectedReferences extends GhidraScript {
    private static String cell(String value) {
        return value.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ');
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) {
            throw new IllegalArgumentException("Expected ranges.tsv (address, decimal byte count, label) and output.tsv");
        }
        int count = 0;
        try (BufferedWriter writer = Files.newBufferedWriter(Path.of(args[1]), StandardCharsets.UTF_8)) {
            writer.write("range\tdestination\tsource\ttype\tfunction\tinstruction\n");
            for (String line : Files.readAllLines(Path.of(args[0]), StandardCharsets.UTF_8)) {
                monitor.checkCancelled();
                if (line.isBlank() || line.startsWith("#")) continue;
                String[] fields = line.split("\t", 3);
                if (fields.length != 3) throw new IllegalArgumentException("Malformed range: " + line);
                long size = Long.parseLong(fields[1]);
                if (size < 1 || size > 1048576) throw new IllegalArgumentException("Invalid range size");
                Address start = toAddr(fields[0]);
                Address end = start.addNoWrap(size - 1);
                if (!currentProgram.getMemory().contains(start, end)) {
                    throw new IllegalArgumentException("Range is outside program memory: " + line);
                }
                int rangeCount = 0;
                var destinations = currentProgram.getReferenceManager().getReferenceDestinationIterator(start, true);
                while (destinations.hasNext()) {
                    monitor.checkCancelled();
                    Address destination = destinations.next();
                    if (destination.compareTo(end) > 0) break;
                    var references = currentProgram.getReferenceManager().getReferencesTo(destination);
                    while (references.hasNext()) {
                        Reference reference = references.next();
                        Address source = reference.getFromAddress();
                        Function function = getFunctionContaining(source);
                        Instruction instruction = getInstructionAt(source);
                        writer.write(cell(fields[2]) + "\t" + destination + "\t" + source + "\t"
                            + reference.getReferenceType() + "\t"
                            + (function == null ? "" : cell(function.getName(true))) + "\t"
                            + (instruction == null ? "" : cell(instruction.toString())) + "\n");
                        rangeCount++;
                    }
                }
                println("OCTA_REFERENCE_RANGE: " + fields[2] + " " + rangeCount);
                count += rangeCount;
            }
        }
        println("OCTA_REFERENCES_OK: " + count + " analyzed references; unresolved indirect targets remain unproven");
    }
}
