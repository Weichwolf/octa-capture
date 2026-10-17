// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <iomanip>
#include <iostream>
#include <string_view>

namespace {

void print_preamp(const octa::PreampState &preamp) {
  const auto &comp = preamp.compressor;
  std::cout << std::boolalpha << R"({"phantom":)" << preamp.phantom << R"(,"phase_inverted":)"
            << preamp.phase_inverted << R"(,"hi_z":)" << preamp.hi_z << R"(,"low_cut":)"
            << preamp.low_cut << R"(,"sensitivity_index":)"
            << static_cast<unsigned int>(preamp.sensitivity_index) << R"(,"compressor":{"enabled":)"
            << comp.enabled << R"(,"gate_index":)" << static_cast<unsigned int>(comp.gate_index)
            << R"(,"attack_index":)" << static_cast<unsigned int>(comp.attack_index)
            << R"(,"release_index":)" << static_cast<unsigned int>(comp.release_index)
            << R"(,"threshold_index":)" << static_cast<unsigned int>(comp.threshold_index)
            << R"(,"ratio_index":)" << static_cast<unsigned int>(comp.ratio_index)
            << R"(,"gain_index":)" << static_cast<unsigned int>(comp.gain_index) << "}}";
}

}

int main(int argc, char **argv) {
  if (argc > 2) {
    std::cerr << "Usage: octa-state [hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1]\n";
    return 2;
  }
  const std::string_view port = argc == 2 ? argv[1] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  const auto state = octa::read_bulk_state(port);
  if (!state) {
    std::cerr << state.error().operation << ": ALSA error " << state.error().code << '\n';
    return 1;
  }
  const auto bank = octa::decode_preamps(*state);
  if (!bank) {
    std::cerr << "Invalid preamp state in bulk reply.\n";
    return 1;
  }
  std::cout << R"({"payload_hex":")" << std::hex << std::setfill('0');
  for (const auto byte : state->payload) {
    std::cout << std::setw(2) << static_cast<unsigned int>(byte);
  }
  std::cout << std::dec << R"(","preamps":[)";
  for (const auto &preamp : bank->channels) {
    if (&preamp != &bank->channels.front()) { std::cout << ','; }
    print_preamp(preamp);
  }
  std::cout << R"(],"stereo_links":[)";
  bool first = true;
  for (const auto linked : bank->stereo_links) {
    if (!first) { std::cout << ','; }
    std::cout << std::boolalpha << linked;
    first = false;
  }
  std::cout << "]}\n";
  return 0;
}
