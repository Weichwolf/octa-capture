// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Bridge.hpp"
#include "octa/Midi.hpp"
#include "octa/PanelCommand.hpp"
#include "octa/StateJson.hpp"

#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <variant>

namespace {

struct Options {
  octa::PanelCommand command;
  std::string_view port;
};

std::optional<Options> options(int argc, char **argv) {
  if (argc < 2 || argc > 4) { return std::nullopt; }
  const std::string_view operation(argv[1]);
  std::string line = "1 ";
  line += operation;
  std::string_view port = "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  if (operation == "display" && argc >= 3) {
    line += ' ';
    line += argv[2];
    if (argc == 4) { port = argv[3]; }
  } else if (operation == "identify" && argc <= 3) {
    if (argc == 3) { port = argv[2]; }
  } else {
    return std::nullopt;
  }
  const auto request = octa::parse_bridge_request(line);
  if (!request) { return std::nullopt; }
  const auto *command = std::get_if<octa::PanelCommand>(&request->command);
  if (command == nullptr) { return std::nullopt; }
  return Options{.command = *command, .port = port};
}

}

int main(int argc, char **argv) {
  const auto selected = options(argc, argv);
  if (!selected) {
    std::cerr << "Usage: octa-panel display BUS:0..3 [hw:CTRL-port]\n"
                 "       octa-panel identify [hw:CTRL-port]\n";
    return 2;
  }
  auto session = octa::MidiSession::open(selected->port);
  if (!session) {
    std::cerr << session.error().operation << ": ALSA error " << session.error().code << '\n';
    return 1;
  }
  const auto state = session->panel_command(selected->command);
  if (!state) {
    std::cerr << state.error().operation << ": ALSA error " << state.error().code << '\n';
    return 1;
  }
  return octa::write_state_json(std::cout, *state) ? 0 : 1;
}
