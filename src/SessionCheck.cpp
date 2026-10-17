// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Dsp.hpp"
#include "octa/DspControl.hpp"
#include "octa/Meters.hpp"
#include "octa/Midi.hpp"
#include "octa/Protocol.hpp"

#include <cerrno>
#include <chrono>
#include <cstddef>
#include <expected>
#include <iostream>
#include <string_view>

namespace {

struct Counts {
  std::size_t levels = 0;
  std::size_t side_messages = 0;
};

bool is_meter(const octa::Message &message) {
  return message.command == octa::Command::data && message.address == octa::Address{0, 10, 0, 1};
}

std::expected<Counts, octa::MidiError> check_session(octa::MidiSession &session,
                                                     const octa::Message &baseline) {
  const auto dsp = octa::decode_dsp(baseline);
  if (!dsp) {
    return std::unexpected(octa::MidiError{.code = -EBADMSG, .operation = "session DSP state"});
  }
  const octa::MixerChange pan = {.source = octa::MixerSource::input,
                                 .bus = 0,
                                 .channel = 0,
                                 .parameter = octa::MixerParameter::pan,
                                 .value = dsp->mixers.front().input.channels.front().pan};
  Counts counts;
  bool malformed = false;
  const auto observe = [&](const octa::Message &message) {
    if (is_meter(message)) {
      if (octa::decode_input_meters(message)) {
        ++counts.levels;
      } else {
        malformed = true;
      }
    }
  };
  const octa::MessageListener listener = [&](const octa::Message &message) {
    ++counts.side_messages;
    observe(message);
  };
  constexpr std::size_t query_count = 8;
  for (std::size_t query = 0; query < query_count; ++query) {
    auto message = session.receive(std::chrono::seconds(1));
    if (!message) { return std::unexpected(message.error()); }
    observe(*message);
    // Alternate read-only queries with an idempotent write of the current pan.
    const auto state =
        query % 2 == 0 ? session.read_bulk_state(listener) : session.write_dsp(pan, listener);
    if (!state) { return std::unexpected(state.error()); }
    if (*state != baseline) {
      return std::unexpected(octa::MidiError{.code = -EIO, .operation = "session state changed"});
    }
    if (malformed) {
      return std::unexpected(octa::MidiError{.code = -EBADMSG, .operation = "session meter data"});
    }
  }
  if (counts.levels == 0) {
    return std::unexpected(octa::MidiError{.code = -ETIMEDOUT, .operation = "session levels"});
  }
  return counts;
}

std::expected<std::size_t, octa::MidiError> wait_for_quiet(octa::MidiSession &session) {
  constexpr auto settling_timeout = std::chrono::seconds(3);
  constexpr auto quiet_interval = std::chrono::milliseconds(200);
  const auto deadline = std::chrono::steady_clock::now() + settling_timeout;
  std::size_t trailing = 0;
  while (std::chrono::steady_clock::now() < deadline) {
    const auto message = session.receive(quiet_interval);
    if (!message) {
      if (message.error().code == -ETIMEDOUT) { return trailing; }
      return std::unexpected(message.error());
    }
    if (!is_meter(*message) || !octa::decode_input_meters(*message)) {
      return std::unexpected(octa::MidiError{.code = -EBADMSG, .operation = "meter stop traffic"});
    }
    ++trailing;
  }
  return std::unexpected(octa::MidiError{.code = -ETIMEDOUT, .operation = "meter stop settling"});
}

int failure(const octa::MidiError &error) {
  std::cerr << error.operation << ": ALSA error " << error.code << '\n';
  return 1;
}

}

int main(int argc, char **argv) {
  if (argc > 2) {
    std::cerr << "Usage: octa-session-check [hw:CTRL-port]\n";
    return 2;
  }
  const std::string_view port = argc == 2 ? argv[1] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  auto session = octa::MidiSession::open(port);
  if (!session) { return failure(session.error()); }
  const auto baseline = session->read_bulk_state();
  if (!baseline) { return failure(baseline.error()); }
  const auto enabled = session->set_input_meters(true);
  if (!enabled) { return failure(enabled.error()); }
  const auto result = check_session(*session, *baseline);
  const auto disabled = session->set_input_meters(false);
  if (!disabled) { return failure(disabled.error()); }
  if (!result) { return failure(result.error()); }
  const auto quiet = wait_for_quiet(*session);
  if (!quiet) { return failure(quiet.error()); }
  std::cout << "Shared CTRL session: eight bulk queries including four same-value pan writes, "
            << result->levels << " valid meter packets, " << result->side_messages
            << " notifications during queries; " << *quiet
            << " trailing meter packets before quiet; complete bulk state unchanged.\n";
  return 0;
}
