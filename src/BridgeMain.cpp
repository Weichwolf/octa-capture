// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Bridge.hpp"
#include "octa/DspControl.hpp"
#include "octa/InputMonitor.hpp"
#include "octa/Meters.hpp"
#include "octa/Midi.hpp"
#include "octa/PanelCommand.hpp"
#include "octa/PcmMeterControl.hpp"
#include "octa/PcmMeters.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"
#include "octa/StateJson.hpp"

#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <iostream>
#include <optional>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <sys/poll.h>
#include <type_traits>
#include <unistd.h>
#include <utility>
#include <variant>

extern "C" {
#include <signal.h>
}

namespace {

volatile sig_atomic_t interrupted = 0;

void interrupt(int /* signal */) {
  interrupted = 1;
}

bool install_handlers() {
  struct sigaction action{};
  action.sa_handler = &interrupt;
  if (sigemptyset(&action.sa_mask) != 0 || sigaction(SIGINT, &action, nullptr) != 0 ||
      sigaction(SIGTERM, &action, nullptr) != 0) {
    return false;
  }
  action.sa_handler = SIG_IGN;
  return sigaction(SIGPIPE, &action, nullptr) == 0;
}

void error_reply(std::uint32_t serial, octa::MidiError error) {
  std::cerr << error.operation << ": ALSA error " << error.code << '\n';
  std::cout << R"({"id":)" << serial << R"(,"ok":false,"code":)" << error.code
            << R"(,"error":"Device command failed."})" << '\n'
            << std::flush;
}

void notification(const octa::Message &message) {
  if (octa::write_auto_sense_activity_json(std::cout, message)) {
    std::cout << '\n' << std::flush;
    return;
  }
  const auto meters = octa::decode_input_meters(message);
  if (!meters) { return; }
  std::cout << R"({"event":"meters","input_levels":[)";
  for (const auto &level : *meters) {
    if (&level != &meters->front()) { std::cout << ','; }
    std::cout << level;
  }
  std::cout << R"(],"input_db":[)";
  for (const auto &level : *meters) {
    if (&level != &meters->front()) { std::cout << ','; }
    const auto decibels = octa::input_meter_db(level);
    if (decibels) {
      std::cout << *decibels;
    } else {
      std::cout << "null";
    }
  }
  std::cout << "]}\n" << std::flush;
}

std::expected<octa::Message, octa::MidiError> execute(octa::MidiSession &session,
                                                      const octa::BridgeCommand &command) {
  return std::visit(
      [&session](const auto &change) -> std::expected<octa::Message, octa::MidiError> {
        using Change = std::remove_cvref_t<decltype(change)>;
        if constexpr (std::is_same_v<Change, octa::StateQuery>) {
          return session.read_bulk_state(notification);
        } else if constexpr (std::is_same_v<Change, octa::PreampChange>) {
          return session.write_preamp(change, notification);
        } else if constexpr (std::is_same_v<Change, octa::DspChange>) {
          return session.write_dsp(change, notification);
        } else if constexpr (std::is_same_v<Change, octa::PanelCommand>) {
          return session.panel_command(change, notification);
        } else if constexpr (std::is_same_v<Change, octa::InputMonitorChange>) {
          return session.write_input_monitor(change, notification);
        } else {
          return std::unexpected(octa::MidiError{.code = -EINVAL, .operation = "bridge command"});
        }
      },
      command);
}

bool state_reply(std::uint32_t serial,
                 const octa::Message &message,
                 std::optional<bool> stop_sent) {
  std::ostringstream state;
  if (!octa::write_state_json(state, message)) {
    error_reply(serial, {.code = -EBADMSG, .operation = "invalid bridge state"});
    return false;
  }
  std::cout << R"({"id":)" << serial << R"(,"ok":true,"state":)" << state.view();
  if (stop_sent) { std::cout << R"(,"auto_sense_stop_sent":)" << std::boolalpha << *stop_sent; }
  std::cout << "}\n" << std::flush;
  return std::cout.good();
}

bool dispatch(octa::MidiSession &session, const octa::BridgeRequest &request) {
  if (const auto *stop = std::get_if<octa::AutoSenseStop>(&request.command)) {
    const auto result = session.stop_observed_auto_sense(stop->action, notification);
    if (!result) {
      error_reply(request.serial, result.error());
      return false;
    }
    return state_reply(request.serial, result->state, result->stop_sent);
  }
  if (const auto *meters = std::get_if<octa::MeterSwitch>(&request.command)) {
    const auto result = session.set_input_meters(meters->enabled);
    if (!result) {
      error_reply(request.serial, result.error());
      return false;
    }
    std::cout << R"({"id":)" << request.serial << R"(,"ok":true})" << '\n' << std::flush;
    return std::cout.good();
  }
  const auto result = execute(session, request.command);
  if (!result) {
    error_reply(request.serial, result.error());
    return false;
  }
  return state_reply(request.serial, *result, std::nullopt);
}

bool input(octa::MidiSession &session, std::string &pending, std::span<const char> bytes) {
  for (const char byte : bytes) {
    if (byte == '\n') {
      const auto request = octa::parse_bridge_request(pending);
      pending.clear();
      if (!request) {
        error_reply(0, {.code = -EINVAL, .operation = "invalid bridge request"});
      } else if (!dispatch(session, *request)) {
        return false;
      }
    } else {
      if (pending.size() == octa::bridge_line_limit) {
        error_reply(0, {.code = -EMSGSIZE, .operation = "bridge request limit"});
        return false;
      }
      pending += byte;
    }
  }
  return std::cout.good();
}

void pcm_notification(std::optional<octa::PcmMeterControl> &control) {
  if (!control) { return; }
  const auto meters = control->read();
  if (!meters) {
    std::cerr << meters.error().operation << ": ALSA error " << meters.error().code << '\n';
    control.reset();
    return;
  }
  if (!meters->enabled) {
    const auto enabled = control->enable(true);
    if (!enabled) {
      control.reset();
      return;
    }
  }
  std::cout << R"({"event":"pcm-meters","pcm":)";
  octa::write_pcm_meters_json(std::cout, *meters);
  std::cout << "}\n" << std::flush;
}

bool serve(octa::MidiSession &session, std::optional<octa::PcmMeterControl> &control) {
  std::string pending;
  std::array<char, octa::bridge_line_limit> bytes{};
  auto next_pcm = std::chrono::steady_clock::now();
  while (interrupted == 0 && std::cout.good()) {
    struct pollfd descriptor{.fd = STDIN_FILENO, .events = POLLIN, .revents = 0};
    const int ready = poll(&descriptor, 1, 0);
    if (ready < 0) { return errno == EINTR; }
    if (ready > 0) {
      if ((descriptor.revents & (POLLERR | POLLNVAL)) != 0) { return false; }
      const auto count = read(STDIN_FILENO, bytes.data(), bytes.size());
      if (count == 0) { return pending.empty(); }
      if (count < 0) { return errno == EINTR; }
      if (!input(session, pending, std::span(bytes).first(static_cast<std::size_t>(count)))) {
        return false;
      }
    }
    const auto message = session.receive(std::chrono::milliseconds(5));
    if (message) {
      notification(*message);
    } else if (message.error().code != -ETIMEDOUT && interrupted == 0) {
      error_reply(0, message.error());
      return false;
    }
    if (std::chrono::steady_clock::now() >= next_pcm) {
      pcm_notification(control);
      constexpr auto interval = std::chrono::milliseconds(30);
      next_pcm = std::chrono::steady_clock::now() + interval;
    }
  }
  return std::cout.good();
}

}

int main(int argc, char **argv) {
  if (argc > 2) {
    std::cerr << "Usage: octa-bridge [hw:CTRL-port]\n";
    return 2;
  }
  if (!install_handlers()) { return 1; }
  const std::string_view port = argc == 2 ? argv[1] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  auto session = octa::MidiSession::open(port);
  if (!session) {
    error_reply(0, session.error());
    return 1;
  }
  // Strip only the raw-MIDI selectors, preserving a numeric or named CARD.
  const auto separator = port.find(',');
  std::optional<octa::PcmMeterControl> control;
  // Older modules can still provide every MIDI control without PCM meters.
  auto candidate = octa::PcmMeterControl::open(port.substr(0, separator));
  if (candidate && candidate->enable(true)) { control.emplace(std::move(*candidate)); }
  std::cout << R"({"event":"ready"})" << '\n' << std::flush;
  return serve(*session, control) ? 0 : 1;
}
