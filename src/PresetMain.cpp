// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"
#include "octa/Preset.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <fstream>
#include <ios>
#include <iostream>
#include <span>
#include <string_view>

namespace {

std::expected<octa::Preset, std::string_view> load_file(const char *path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) { return std::unexpected("Could not open preset file."); }
  // Read at most one byte beyond the supported size, even for a huge file.
  std::array<std::uint8_t, octa::preset_size + 1> bytes{};
  input.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  const auto count = input.gcount();
  if (input.bad() || count < 0) { return std::unexpected("Could not read preset file."); }
  const auto preset = octa::decode_preset(std::span(bytes).first(static_cast<std::size_t>(count)));
  if (preset) { return *preset; }
  if (preset.error() == octa::PresetError::size) {
    return std::unexpected("Ver100 preset must contain exactly 1639 bytes.");
  }
  if (preset.error() == octa::PresetError::header) {
    return std::unexpected("Invalid OCTA-CAPTURE preset header.");
  }
  return std::unexpected("Unsupported preset version.");
}

void print_binary(const octa::Preset &preset) {
  const auto bytes = octa::encode_preset(preset);
  std::cout.write(reinterpret_cast<const char *>(bytes.data()),
                  static_cast<std::streamsize>(bytes.size()));
}

int finish_output() {
  std::cout.flush();
  if (!std::cout.good()) {
    std::cerr << "Could not write preset output.\n";
    return 1;
  }
  return 0;
}

int export_current(std::string_view port) {
  const auto state = octa::read_bulk_state(port);
  if (!state) {
    std::cerr << state.error().operation << ": ALSA error " << state.error().code << '\n';
    return 1;
  }
  const auto preset = octa::preset_from_bulk(*state);
  if (!preset) {
    std::cerr << "Could not convert device bulk state to a preset.\n";
    return 1;
  }
  print_binary(*preset);
  return finish_output();
}

int prepare_file(const octa::Preset &preset, const char *bulk_path) {
  std::ifstream input(bulk_path, std::ios::binary);
  constexpr std::size_t frame_size = octa::bulk_size + 13;
  std::array<std::uint8_t, frame_size + 1> bytes{};
  input.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  const auto count = input.gcount();
  if (input.bad() || count < 0) {
    std::cerr << "Could not read current bulk frame.\n";
    return 1;
  }
  const auto current = octa::decode(std::span(bytes).first(static_cast<std::size_t>(count)));
  if (!current) {
    std::cerr << "Invalid current bulk frame.\n";
    return 1;
  }
  const auto target = octa::preset_to_bulk(preset, *current);
  if (!target) {
    std::cerr << "Could not prepare import: invalid current state or preset control bounds.\n";
    return 1;
  }
  const auto wire = octa::encode(target->command, target->address, target->payload);
  if (!wire) {
    std::cerr << "Could not encode prepared import.\n";
    return 1;
  }
  std::cout.write(reinterpret_cast<const char *>(wire->data()),
                  static_cast<std::streamsize>(wire->size()));
  return finish_output();
}

void print_summary(const octa::Preset &preset) {
  std::cout << R"({"format":"Ver100","bytes":)" << octa::preset_size << R"(,"output_routes":[)";
  for (const auto &route : preset.output_routes) {
    if (&route != &preset.output_routes.front()) { std::cout << ','; }
    std::cout << route;
  }
  std::cout << R"(],"reverb_type":)" << preset.reverb.type << R"(,"preamp_pairs":)"
            << preset.preamps.size() << R"(,"mixer_buses":)" << preset.mixers.size() << "}\n";
}

}

int main(int argc, char **argv) {
  const std::string_view mode = argc >= 2 ? argv[1] : "";
  if (mode == "export" && (argc == 2 || argc == 3)) {
    const std::string_view port = argc == 3 ? argv[2] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
    return export_current(port);
  }
  const bool file_mode = mode == "inspect" || mode == "normalize" || mode == "prepare";
  const int required_arguments = mode == "prepare" ? 4 : 3;
  if (argc != required_arguments || !file_mode) {
    std::cerr << "Usage: octa-preset inspect|normalize FILE\n"
                 "       octa-preset export [hw:CTRL-port] > settings.octa\n"
                 "       octa-preset prepare FILE CURRENT.syx > prepared.syx (offline)\n"
                 "normalize and export write binary Ver100 files to stdout.\n";
    return 2;
  }
  const auto preset = load_file(argv[2]);
  if (!preset) {
    std::cerr << preset.error() << '\n';
    return 1;
  }
  if (mode == "prepare") { return prepare_file(*preset, argv[3]); }
  if (mode == "inspect") {
    print_summary(*preset);
  } else {
    print_binary(*preset);
  }
  return finish_output();
}
