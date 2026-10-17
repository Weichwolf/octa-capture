// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"
#include "octa/StateJson.hpp"

#include <iostream>
#include <ostream>
#include <string_view>

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
  if (!octa::write_state_json(std::cout, *state)) {
    std::cerr << "Invalid state or JSON output failure.\n";
    return 1;
  }
  std::cout << '\n' << std::flush;
  return std::cout.good() ? 0 : 1;
}
