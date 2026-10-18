// SPDX-License-Identifier: GPL-2.0-only
#include "../src/PresetDevice.hpp"
#include "octa/AutoSense.hpp"
#include "octa/DirectOut.hpp"
#include "octa/DspControl.hpp"
#include "octa/Initialization.hpp"
#include "octa/InputMonitor.hpp"
#include "octa/Meters.hpp"
#include "octa/Midi.hpp"
#include "octa/PanelCommand.hpp"
#include "octa/Preamp.hpp"
#include "octa/Preset.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iostream>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <sys/poll.h>
#include <sys/types.h>
#include <system_error>
#include <unistd.h>
#include <utility>
#include <vector>

extern "C" {
#include <signal.h>
#include <stdlib.h>
}

namespace {

struct FakeIo {
  std::vector<std::uint8_t> incoming;
  std::vector<std::uint8_t> outgoing;
  std::vector<std::vector<std::uint8_t>> replies;
  octa::SysExReceiver requests;
  std::size_t reply_position = 0;
  unsigned int opens = 0;
  std::size_t position = 0;
  std::size_t read_size = 1;
  std::size_t write_size = 3;
  unsigned int writes = 0;
  unsigned int closes = 0;
  unsigned int fail_write = 0;
  int open_error = 0;
  int read_error = 0;
  std::vector<std::uint8_t> auto_start_reply;
  std::vector<std::uint8_t> auto_stop_reply;
  std::vector<std::uint8_t> auto_stop_pending;
  int auto_signal = 0;
  bool auto_fail_stop = false;
  bool require_auto_completion = false;
  bool reject_observed_stop = false;
  std::size_t auto_completion_end = 0;
  unsigned int route_commands = 0;
  unsigned int fail_route = 0;
  unsigned int interrupt_route = 0;
  int memory_signal = 0;
  bool fail_after_memory = false;
  int initialization_signal = 0;
  bool fail_after_initialization = false;
  unsigned int read_failures = 0;
  bool oversized_read = false;
  bool oversized_write = false;
};

FakeIo io;
std::max_align_t input_token{};
std::max_align_t output_token{};
std::array<int, 2> wake_pipe = {-1, -1};

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

std::vector<std::uint8_t> switch_frame(bool enabled) {
  const auto frame = octa::encode_meter_switch(enabled);
  return frame ? *frame : std::vector<std::uint8_t>{};
}

std::vector<std::uint8_t> level_frame(bool malformed = false) {
  constexpr std::size_t meter_bytes = 16;
  const std::vector<std::uint8_t> payload(malformed ? meter_bytes - 1 : meter_bytes);
  const auto frame = octa::encode(octa::Command::data, {0, 10, 0, 1}, payload);
  return frame ? *frame : std::vector<std::uint8_t>{};
}

bool sent_stop(std::size_t prefix) {
  const auto stop = switch_frame(false);
  return io.outgoing.size() == prefix + stop.size() &&
         std::ranges::equal(std::span(io.outgoing).subspan(prefix), stop);
}

bool successful_stream() {
  io = {};
  io.incoming = switch_frame(true); // Unrelated but valid notification.
  const auto levels = level_frame();
  io.incoming.insert(io.incoming.end(), levels.begin(), levels.end());
  unsigned int callbacks = 0;
  const auto result = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [&callbacks](const octa::InputMeters &meters) {
        ++callbacks;
        return meters != octa::InputMeters{};
      });
  const auto start = switch_frame(true);
  return require(result && *result == 1 && callbacks == 1 && io.closes == 2 &&
                     sent_stop(start.size()) &&
                     std::ranges::equal(std::span(io.outgoing).first(start.size()), start),
                 "partial reads/writes, unrelated messages, callback cancellation and stop");
}

bool input_failure(bool malformed) {
  io = {};
  io.incoming = level_frame(malformed);
  io.read_error = malformed ? 0 : -EIO;
  unsigned int callbacks = 0;
  const auto result = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [&callbacks](const octa::InputMeters &) {
        ++callbacks;
        return true;
      });
  const int expected = malformed ? -EBADMSG : -EIO;
  return require(!result && result.error().code == expected && callbacks == 0 && io.closes == 2 &&
                     sent_stop(switch_frame(true).size()),
                 "read errors and malformed packets still send the disable command");
}

bool write_failures() {
  io = {};
  io.fail_write = 2; // Fail after the first three bytes of the enable frame.
  const auto partial = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [](const octa::InputMeters &) { return false; });
  if (!require(!partial && partial.error().code == -EIO && io.closes == 2 &&
                   sent_stop(io.write_size),
               "failed partial enable is followed by a complete disable attempt")) {
    return false;
  }
  io = {};
  io.incoming = level_frame();
  io.write_size = switch_frame(true).size();
  io.fail_write = 2; // The complete enable succeeds, then the stop fails.
  const auto stop = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [](const octa::InputMeters &) { return false; });
  return require(!stop && stop.error().code == -EIO && io.writes == 3 && io.closes == 2 &&
                     sent_stop(switch_frame(true).size()),
                 "stop failure is reported; destruction retries cleanup before both handles close");
}

bool deadlines() {
  constexpr auto normal_duration = std::chrono::milliseconds(50);
  constexpr auto empty_duration = std::chrono::milliseconds(20);
  io = {};
  io.read_size = level_frame().size();
  io.incoming = level_frame();
  unsigned int callbacks = 0;
  const auto normal = octa::observe_input_meters(
      "hw:fake", normal_duration, [&callbacks](const octa::InputMeters &) {
        ++callbacks;
        return true;
      });
  if (!require(normal && *normal == 1 && callbacks == 1 && io.closes == 2 &&
                   sent_stop(switch_frame(true).size()),
               "requested duration completes successfully after valid packets and an empty poll")) {
    return false;
  }
  io = {};
  const auto empty = octa::observe_input_meters(
      "hw:fake", empty_duration, [](const octa::InputMeters &) { return true; });
  return require(!empty && empty.error().code == -ETIMEDOUT && io.closes == 2 &&
                     sent_stop(switch_frame(true).size()),
                 "no packets is a timeout and still sends the stop command");
}

void append_bytes(std::vector<std::uint8_t> &destination, std::span<const std::uint8_t> bytes) {
  destination.insert(destination.end(), bytes.begin(), bytes.end());
}

std::vector<std::uint8_t> bulk_frame(std::uint8_t marker = 0) {
  std::vector<std::uint8_t> payload(octa::bulk_size);
  payload.front() = marker;
  const auto frame = octa::encode(octa::Command::data, octa::bulk_address, payload);
  return frame ? *frame : std::vector<std::uint8_t>{};
}

bool panel_operations() {
  constexpr std::array commands = {
      octa::PanelCommand{.action = octa::PanelAction::select_mixer, .bus = 0},
      octa::PanelCommand{.action = octa::PanelAction::select_mixer, .bus = 3},
      octa::PanelCommand{.action = octa::PanelAction::identify}};
  for (const auto command : commands) {
    for (const bool report_selection : {false, true}) {
      io = {};
      auto before = octa::decode(bulk_frame());
      if (!before) { return false; }
      before->payload[4] = 1;
      auto after = *before;
      if (report_selection && command.action == octa::PanelAction::select_mixer) {
        after.payload[4] = command.bus;
      }
      const auto initial = octa::encode(before->command, before->address, before->payload);
      const auto final = octa::encode(after.command, after.address, after.payload);
      const auto query = octa::request(octa::bulk_address, octa::bulk_size);
      const auto frame = octa::encode_panel_command(command);
      if (!initial || !final || !query || !frame) { return false; }
      io.replies = {*initial, *final};
      auto session = octa::MidiSession::open("hw:fake");
      if (!session) { return false; }
      const auto result = session->panel_command(command);
      auto expected = *query;
      append_bytes(expected, *frame);
      append_bytes(expected, *query);
      if (!require(
              result && *result == after && io.outgoing == expected,
              "panel command is bracketed by two fresh queries; LCD bulk cache may be stale")) {
        return false;
      }
    }
  }
  return true;
}

bool panel_rejection() {
  io = {};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto invalid =
      session->panel_command({.action = octa::PanelAction::select_mixer, .bus = 4});
  if (!require(!invalid && invalid.error().code == -EINVAL && io.writes == 0,
               "invalid panel selector rejected before I/O")) {
    return false;
  }
  auto before = octa::decode(bulk_frame());
  if (!before) { return false; }
  before->payload[2] = 3;
  const auto frame = octa::encode(before->command, before->address, before->payload);
  if (!frame) { return false; }
  io.replies = {*frame};
  const auto high_rate =
      session->panel_command({.action = octa::PanelAction::select_mixer, .bus = 1});
  const auto query = octa::request(octa::bulk_address, octa::bulk_size);
  return require(!high_rate && high_rate.error().code == -EINVAL && query && io.outgoing == *query,
                 "192 kHz permits only mixer A, checked before a selection write");
}

bool panel_failures() {
  io = {};
  auto changed = octa::decode(bulk_frame());
  if (!changed) { return false; }
  constexpr std::size_t volume_offset = 0x120;
  changed->payload[volume_offset] = 1;
  const auto encoded = octa::encode(changed->command, changed->address, changed->payload);
  if (!encoded) { return false; }
  io.replies = {bulk_frame(), *encoded};
  {
    auto session = octa::MidiSession::open("hw:fake");
    if (!session) { return false; }
    const auto failed = session->panel_command({.action = octa::PanelAction::identify});
    if (!require(!failed && failed.error().code == -EIO,
                 "unrelated audio change is not a successful panel command")) {
      return false;
    }
  }
  io = {};
  constexpr std::size_t whole_write = 512;
  io.write_size = whole_write;
  io.fail_write = 2;
  io.replies = {bulk_frame()};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto failed = session->panel_command({.action = octa::PanelAction::identify});
  const auto writes = io.writes;
  const auto retry = session->read_bulk_state();
  return require(!failed && failed.error().code == -EIO && !retry &&
                     retry.error().code == -EPROTO && io.writes == writes,
                 "failed panel transfer requires a fresh session");
}

bool session_buffering() {
  io = {};
  constexpr std::size_t whole_chunk = 512;
  io.read_size = whole_chunk;
  io.incoming = level_frame();
  append_bytes(io.incoming, level_frame());
  {
    auto session = octa::MidiSession::open("hw:fake");
    if (!session || !session->set_input_meters(true)) { return false; }
    const auto first = session->receive(std::chrono::seconds(1));
    const bool all_read = io.position == io.incoming.size();
    const auto second = session->receive(std::chrono::seconds(1));
    if (!require(first && second && *first == *second && all_read && io.opens == 1,
                 "two complete packets in one read remain available across receive calls")) {
      return false;
    }
    auto moved = std::move(*session);
    const auto closed = session->receive(std::chrono::milliseconds(1));
    if (!require(!closed && closed.error().code == -ENODEV && io.closes == 0,
                 "moving the session transfers ownership without closing either handle")) {
      return false;
    }
    const auto frame = level_frame();
    constexpr std::size_t split = 12;
    append_bytes(io.incoming, std::span(frame).first(split));
    const auto partial = moved.receive(std::chrono::milliseconds(2));
    append_bytes(io.incoming, std::span(frame).subspan(split));
    const auto recovered = moved.receive(std::chrono::seconds(1));
    if (!require(!partial && partial.error().code == -ETIMEDOUT && recovered &&
                     *recovered == *first,
                 "partial frames survive a receive timeout and a moved owner")) {
      return false;
    }
  }
  return require(io.closes == 2 && sent_stop(switch_frame(true).size()),
                 "destruction disables an active meter stream before closing handles");
}

bool session_queries() {
  io = {};
  constexpr std::size_t whole_chunk = 512;
  io.read_size = whole_chunk;
  io.incoming = level_frame();
  append_bytes(io.incoming, bulk_frame(1)); // Complete stale reply, already queued.
  auto reply = level_frame();
  append_bytes(reply, bulk_frame(2));
  append_bytes(reply, level_frame());
  io.replies.push_back(reply);
  auto session = octa::MidiSession::open("hw:fake");
  if (!session || !session->set_input_meters(true)) { return false; }
  unsigned int notifications = 0;
  bool busy = true;
  const auto state = session->read_bulk_state([&](const octa::Message &) {
    ++notifications;
    const auto nested = session->read_bulk_state();
    const auto receive = session->receive(std::chrono::milliseconds(1));
    const auto disable = session->set_input_meters(false);
    const auto start_auto = session->start_auto_sense(1);
    const auto stop_auto = session->stop_auto_sense(octa::AutoSenseAction::cancel);
    busy = busy && !nested && nested.error().code == -EBUSY && !receive &&
           receive.error().code == -EBUSY && !disable && disable.error().code == -EBUSY &&
           !start_auto && start_auto.error().code == -EBUSY && !stop_auto &&
           stop_auto.error().code == -EBUSY;
  });
  const auto trailing = session->receive(std::chrono::seconds(1));
  const auto disabled = session->set_input_meters(false);
  return require(
      state && state->payload.front() == 2 && notifications == 3 && busy && trailing &&
          octa::decode_input_meters(*trailing) && disabled && io.opens == 1,
      "one connection drains stale replies, dispatches side messages, preserves trailing "
      "packets and rejects reentrant operations");
}

bool session_stale_fragment() {
  io = {};
  const auto old = bulk_frame(1);
  constexpr std::size_t split = 16;
  append_bytes(io.incoming, std::span(old).first(split));
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto partial = session->receive(std::chrono::milliseconds(2));
  std::vector<std::uint8_t> reply;
  append_bytes(reply, std::span(old).subspan(split));
  append_bytes(reply, bulk_frame(2));
  io.replies.push_back(reply);
  unsigned int stale = 0;
  const auto state = session->read_bulk_state([&](const octa::Message &message) {
    if (message.address == octa::bulk_address && message.payload.front() == 1) { ++stale; }
  });
  return require(!partial && partial.error().code == -ETIMEDOUT && state &&
                     state->payload.front() == 2 && stale == 1,
                 "completion of an older partial bulk frame cannot satisfy a new query");
}

bool session_writes() {
  io = {};
  const auto zero = bulk_frame();
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  io.replies = {zero, zero, zero, zero};
  const auto dsp = session->write_dsp(octa::RouteChange{.pair = 0, .selection = 0});
  const auto wrong_dsp = session->write_dsp(octa::RouteChange{.pair = 0, .selection = 1});
  const auto preamp = session->write_preamp(
      {.channel = 0, .parameter = octa::PreampParameter::low_cut, .value = 0});
  const auto wrong_preamp = session->write_preamp(
      {.channel = 0, .parameter = octa::PreampParameter::low_cut, .value = 1});
  const auto before = io.writes;
  constexpr std::uint8_t invalid_pair = 255;
  const auto invalid = session->write_dsp(octa::RouteChange{.pair = invalid_pair, .selection = 0});
  return require(dsp && !wrong_dsp && wrong_dsp.error().code == -EIO && preamp && !wrong_preamp &&
                     wrong_preamp.error().code == -EIO && !invalid &&
                     invalid.error().code == -EINVAL && before == io.writes && io.opens == 1,
                 "typed writes validate before transmission and verify readback on one connection");
}

enum class MonitorScenario : std::uint8_t {
  success,
  unrelated_change,
  highest_rate,
  inactive_input,
  unmapped_input
};

bool input_monitor_session(MonitorScenario scenario) {
  io = {};
  auto before = octa::decode(bulk_frame());
  if (!before) { return false; }
  const bool highest =
      scenario == MonitorScenario::highest_rate || scenario == MonitorScenario::inactive_input;
  before->payload[2] = highest ? 3 : 0;
  std::uint8_t channel = 0;
  if (scenario == MonitorScenario::inactive_input) {
    channel = 4;
  } else if (scenario == MonitorScenario::unmapped_input) {
    channel = 10;
  }
  const octa::InputMonitorChange change = {
      .channel = channel, .enabled = false, .pan = 1, .volume = 1};
  const auto original = octa::encode(before->command, before->address, before->payload);
  const auto target = octa::input_monitor_target(*before, change);
  if (!original) { return false; }
  io.replies = {*original};
  if (target) {
    auto response = *target;
    constexpr std::size_t unrelated = 0x442;
    if (scenario == MonitorScenario::unrelated_change) { response.payload[unrelated] = 1; }
    const auto encoded = octa::encode(response.command, response.address, response.payload);
    if (!encoded) { return false; }
    io.replies.push_back(*encoded);
  }
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto result = session->write_input_monitor(change);
  if (scenario == MonitorScenario::unmapped_input) {
    return require(!result && result.error().code == -EINVAL && io.writes == 0,
                   "unmapped monitor input rejected before any MIDI transmission");
  }
  if (scenario == MonitorScenario::inactive_input) {
    const auto request = octa::request(octa::bulk_address, octa::bulk_size);
    return require(!result && result.error().code == -EINVAL && request && io.outgoing == *request,
                   "inactive monitor input may query state but cannot transmit a change");
  }
  if (scenario == MonitorScenario::unrelated_change) {
    return require(!result && result.error().code == -EIO,
                   "monitor success requires the entire predicted raw state");
  }
  return require(result && target && *result == *target,
                 "monitor request verifies its complete reply");
}

bool auto_sense_session() {
  io = {};
  constexpr std::uint8_t selected = 0x81;
  const auto start = octa::encode_auto_sense_start(selected);
  const auto stop = octa::encode_auto_sense_stop(octa::AutoSenseAction::finish);
  const auto request = octa::request(octa::bulk_address, octa::bulk_size);
  if (!start || !stop || !request) { return false; }
  auto reply = *start;
  append_bytes(reply, bulk_frame());
  io.replies = {reply};
  octa::AutoSenseChannels channels{};
  {
    auto session = octa::MidiSession::open("hw:fake");
    if (!session || !session->start_auto_sense(selected)) { return false; }
    const auto writes = io.writes;
    const auto repeated = session->start_auto_sense(selected);
    const bool blocked = !repeated && repeated.error().code == -EALREADY && io.writes == writes;
    bool valid_notification = false;
    const auto current = session->read_bulk_state([&](const octa::Message &message) {
      const auto updated = octa::apply_auto_sense_update(channels, message);
      valid_notification = updated && *updated;
    });
    const auto finished = session->stop_auto_sense(octa::AutoSenseAction::finish);
    if (!require(blocked && current && finished && valid_notification && channels.front() &&
                     channels.back() && std::ranges::count(channels, true) == 2,
                 "Auto-Sens notifications share a queried CTRL session; repeated starts blocked")) {
      return false;
    }
  }
  auto expected = *start;
  append_bytes(expected, *request);
  append_bytes(expected, *stop);
  return require(io.outgoing == expected && io.opens == 1 && io.closes == 2,
                 "successful finish sends no additional cancellation on destruction");
}

enum class ObservedScenario : std::uint8_t {
  active,
  unknown,
  inactive,
  already_ended,
  reactivated,
  missing_end,
  malformed_end,
  invalid_initial,
  invalid_final,
  restarted,
  write_failure
};

void configure_observed(ObservedScenario scenario,
                        const std::vector<std::uint8_t> &start,
                        const std::vector<std::uint8_t> &end,
                        const std::vector<std::uint8_t> &malformed,
                        const std::vector<std::uint8_t> &invalid_frame) {
  io.incoming = scenario == ObservedScenario::unknown ? std::vector<std::uint8_t>{} : start;
  if (scenario == ObservedScenario::inactive) { io.incoming = end; }
  if (scenario == ObservedScenario::already_ended) { append_bytes(io.incoming, end); }
  if (scenario == ObservedScenario::reactivated) {
    io.incoming = end;
    append_bytes(io.incoming, start);
  }
  io.auto_stop_reply =
      scenario == ObservedScenario::missing_end ? std::vector<std::uint8_t>{} : end;
  if (scenario == ObservedScenario::malformed_end) { io.auto_stop_reply = malformed; }
  io.reject_observed_stop = scenario == ObservedScenario::write_failure;
  io.replies = {bulk_frame(), bulk_frame()};
  if (scenario == ObservedScenario::invalid_initial) { io.replies.front() = invalid_frame; }
  if (scenario == ObservedScenario::invalid_final) { io.replies.back() = invalid_frame; }
  if (scenario == ObservedScenario::restarted) {
    io.replies.back() = start;
    append_bytes(io.replies.back(), bulk_frame());
  }
}

bool observed_auto_sense(octa::AutoSenseAction action, ObservedScenario scenario) {
  io = {};
  const auto start = octa::encode_auto_sense_start(1);
  const auto end = octa::encode_auto_sense_stop(action);
  const auto request = octa::request(octa::bulk_address, octa::bulk_size);
  const auto malformed =
      octa::encode(octa::Command::data, {0, 2, 1, 5}, std::array<std::uint8_t, 2>{});
  auto invalid = octa::decode(bulk_frame());
  if (!start || !end || !request || !malformed || !invalid) { return false; }
  constexpr std::size_t sensitivity_offset = 0x1d;
  constexpr std::uint8_t invalid_sensitivity = 101;
  invalid->payload[sensitivity_offset] = invalid_sensitivity;
  const auto invalid_frame = octa::encode(invalid->command, invalid->address, invalid->payload);
  if (!invalid_frame) { return false; }
  configure_observed(scenario, *start, *end, *malformed, *invalid_frame);
  const bool inactive =
      scenario == ObservedScenario::inactive || scenario == ObservedScenario::already_ended;
  const bool sent = !inactive && scenario != ObservedScenario::unknown &&
                    scenario != ObservedScenario::invalid_initial;
  const bool success =
      inactive || scenario == ObservedScenario::active || scenario == ObservedScenario::reactivated;
  const bool unresolved = scenario == ObservedScenario::missing_end ||
                          scenario == ObservedScenario::malformed_end ||
                          scenario == ObservedScenario::write_failure;
  {
    auto session = octa::MidiSession::open("hw:fake");
    if (!session) { return false; }
    bool reentrant_safe = true;
    const auto result = session->stop_observed_auto_sense(action, [&](const octa::Message &) {
      const auto nested = session->stop_observed_auto_sense(action);
      reentrant_safe = reentrant_safe && !nested && nested.error().code == -EBUSY;
    });
    if (!require(reentrant_safe && result.has_value() == success,
                 "observed stop requires valid state and acknowledged inactivity; callbacks cannot "
                 "reenter")) {
      return false;
    }
    if (result &&
        (!require(result->stop_sent == sent && result->state == *octa::decode(bulk_frame()),
                  "already-ended calibration sends no stop and all success returns fresh state"))) {
      return false;
    }
    if (unresolved) {
      const auto writes = io.writes;
      const auto query = session->read_bulk_state();
      if (!require(!query && query.error().code == -EPROTO && writes == io.writes,
                   "unresolved stop cannot reuse the session for queries")) {
        return false;
      }
    }
  }
  auto expected = *request;
  if (sent) { append_bytes(expected, *end); }
  if (sent && !unresolved) { append_bytes(expected, *request); }
  return require(io.outgoing == expected && io.opens == 1 && io.closes == 2,
                 "external activity never acquires ownership or triggers destructor cancellation");
}

bool auto_sense_cleanup() {
  io = {};
  const auto start = octa::encode_auto_sense_start(1);
  const auto cancel = octa::encode_auto_sense_stop(octa::AutoSenseAction::cancel);
  if (!start || !cancel) { return false; }
  {
    auto session = octa::MidiSession::open("hw:fake");
    if (!session || !session->set_input_meters(true) || !session->start_auto_sense(1)) {
      return false;
    }
    auto moved = std::move(*session);
    const auto repeated = moved.start_auto_sense(1);
    if (!require(!repeated && repeated.error().code == -EALREADY && io.closes == 0,
                 "move transfers active Auto-Sens ownership")) {
      return false;
    }
  }
  auto expected = switch_frame(true);
  append_bytes(expected, *start);
  append_bytes(expected, *cancel);
  append_bytes(expected, switch_frame(false));
  return require(io.outgoing == expected && io.closes == 2,
                 "moved session cancels owned Auto-Sens before meter disable and close");
}

bool auto_sense_failures(bool fail_start) {
  io = {};
  const auto start = octa::encode_auto_sense_start(1);
  const auto finish = octa::encode_auto_sense_stop(octa::AutoSenseAction::finish);
  const auto cancel = octa::encode_auto_sense_stop(octa::AutoSenseAction::cancel);
  if (!start || !finish || !cancel) { return false; }
  constexpr std::size_t partial_bytes = 3;
  {
    auto session = octa::MidiSession::open("hw:fake");
    if (!session) { return false; }
    if (fail_start) { io.fail_write = 2; }
    const auto started = session->start_auto_sense(1);
    if (fail_start) {
      if (!require(!started && started.error().code == -EIO, "failed Auto-Sens start")) {
        return false;
      }
    } else {
      if (!started) { return false; }
      io.fail_write = io.writes + 2;
      const auto stopped = session->stop_auto_sense(octa::AutoSenseAction::finish);
      if (!require(!stopped && stopped.error().code == -EIO, "failed Auto-Sens finish")) {
        return false;
      }
    }
  }
  std::vector<std::uint8_t> expected;
  if (fail_start) {
    append_bytes(expected, std::span(*start).first(partial_bytes));
  } else {
    append_bytes(expected, *start);
    append_bytes(expected, std::span(*finish).first(partial_bytes));
  }
  append_bytes(expected, *cancel);
  return require(io.outgoing == expected && io.closes == 2,
                 "partial start/finish failure retains ownership for destructor cancellation");
}

bool auto_sense_rejection() {
  io = {};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto empty = session->start_auto_sense(0);
  if (!require(!empty && empty.error().code == -EINVAL, "empty Auto-Sens selection")) {
    return false;
  }
  constexpr unsigned int byte_values = 256;
  for (unsigned int value = 2; value < byte_values; ++value) {
    const auto stopped = session->stop_auto_sense(static_cast<octa::AutoSenseAction>(value));
    if (!require(!stopped && stopped.error().code == -EINVAL && io.writes == 0 && io.position == 0,
                 "invalid Auto-Sens selection/action rejected before MIDI I/O")) {
      return false;
    }
  }
  return true;
}

enum class AutoSenseScenario : std::uint8_t {
  success,
  linked_success,
  unselected_peer,
  malformed,
  missing_start,
  missing_completion,
  unrelated_result,
  partial_stop,
  interruption,
  interactive_finish,
  interactive_missing_completion,
  interactive_interruption
};

bool auto_sense_should_fail(octa::AutoSenseAction action, AutoSenseScenario scenario) {
  switch (scenario) {
    case AutoSenseScenario::success:
    case AutoSenseScenario::linked_success:
    case AutoSenseScenario::interactive_finish: return false;
    case AutoSenseScenario::unselected_peer:
    case AutoSenseScenario::unrelated_result: return action == octa::AutoSenseAction::finish;
    case AutoSenseScenario::malformed:
    case AutoSenseScenario::missing_start:
    case AutoSenseScenario::missing_completion:
    case AutoSenseScenario::partial_stop:
    case AutoSenseScenario::interruption:
    case AutoSenseScenario::interactive_missing_completion:
    case AutoSenseScenario::interactive_interruption:
    default: return true;
  }
}

std::vector<std::uint8_t> auto_sense_baseline(AutoSenseScenario scenario) {
  auto frame = bulk_frame();
  auto message = octa::decode(frame);
  if (message && (scenario == AutoSenseScenario::linked_success ||
                  scenario == AutoSenseScenario::unselected_peer)) {
    constexpr std::size_t compressor_link_offset = 0x25;
    message->payload[compressor_link_offset] = 1;
    const auto encoded = octa::encode(message->command, message->address, message->payload);
    if (encoded) { return *encoded; }
  }
  return frame;
}

bool interactive_auto_sense(AutoSenseScenario scenario) {
  return scenario == AutoSenseScenario::interactive_finish ||
         scenario == AutoSenseScenario::interactive_missing_completion ||
         scenario == AutoSenseScenario::interactive_interruption;
}

bool prepare_auto_sense(octa::AutoSenseAction action,
                        AutoSenseScenario scenario,
                        const std::vector<std::uint8_t> &baseline) {
  io = {};
  auto result = octa::decode(baseline);
  const auto start = octa::encode_auto_sense_start(1);
  const auto stop = octa::encode_auto_sense_stop(action);
  if (!result || !start || !stop) { return false; }
  constexpr std::size_t sensitivity_offset = 0x1d;
  constexpr std::size_t low_cut_offset = 0x17;
  result->payload[sensitivity_offset] = 1;
  if (scenario == AutoSenseScenario::unselected_peer) {
    result->payload[sensitivity_offset + 1] = 1;
  }
  if (scenario == AutoSenseScenario::unrelated_result) { result->payload[low_cut_offset] = 1; }
  const auto changed = octa::encode(result->command, result->address, result->payload);
  if (!changed) { return false; }
  const bool changed_reply =
      action == octa::AutoSenseAction::finish &&
      (scenario == AutoSenseScenario::success || scenario == AutoSenseScenario::linked_success ||
       scenario == AutoSenseScenario::unrelated_result ||
       scenario == AutoSenseScenario::unselected_peer ||
       scenario == AutoSenseScenario::interactive_finish);
  io.replies = {baseline, changed_reply ? *changed : baseline, baseline};
  if (scenario != AutoSenseScenario::missing_start) { io.auto_start_reply = *start; }
  if (scenario != AutoSenseScenario::missing_completion &&
      scenario != AutoSenseScenario::interactive_missing_completion) {
    io.auto_stop_reply = *stop;
  }
  if (scenario == AutoSenseScenario::malformed) {
    const std::array<std::uint8_t, 1> wrong_size = {1};
    const auto malformed = octa::encode(octa::Command::data, {0, 2, 1, 4}, wrong_size);
    if (!malformed) { return false; }
    io.auto_start_reply = *malformed;
  }
  io.auto_fail_stop = scenario == AutoSenseScenario::partial_stop;
  io.auto_signal = scenario == AutoSenseScenario::interruption ? SIGTERM : 0;
  io.require_auto_completion = scenario == AutoSenseScenario::interruption ||
                               scenario == AutoSenseScenario::interactive_interruption;
  if (interactive_auto_sense(scenario)) {
    io.auto_signal = scenario == AutoSenseScenario::interactive_interruption ? SIGTERM : SIGUSR1;
  }
  return true;
}

bool auto_sense_workflow(octa::AutoSenseAction action, AutoSenseScenario scenario) {
  const auto baseline = auto_sense_baseline(scenario);
  const auto query = octa::request(octa::bulk_address, octa::bulk_size);
  if (!query || !prepare_auto_sense(action, scenario, baseline)) { return false; }
  const bool interactive = interactive_auto_sense(scenario);
  auto directory = std::to_array("/tmp/octa-auto-sense-test-XXXXXX");
  if (mkdtemp(directory.data()) == nullptr) { return false; }
  const auto path = std::string(directory.data()) + "/recovery.syx";
  const octa::cli::AutoSenseOptions request = {
      .channels = 1,
      .duration = std::chrono::milliseconds(interactive ? 60000 : 1),
      .action = action,
      .interactive = interactive};
  std::stringbuf errors;
  auto *previous_errors = std::cerr.rdbuf(&errors);
  const auto started_at = std::chrono::steady_clock::now();
  const int status =
      octa::cli::run_auto_sense(request, {.port = "hw:fake", .snapshot_path = path.c_str()});
  const bool bounded = std::chrono::steady_clock::now() - started_at < std::chrono::seconds(5);
  std::cerr.rdbuf(previous_errors);
  std::ifstream file(path, std::ios::binary);
  std::array<std::uint8_t, octa::bulk_size + 14> bytes{};
  file.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  const bool saved = file.gcount() == static_cast<std::streamsize>(baseline.size()) &&
                     std::ranges::equal(std::span(bytes).first(baseline.size()), baseline);
  const bool failed = auto_sense_should_fail(action, scenario);
  int expected_status = failed ? 1 : 0;
  constexpr int interrupted_status = 130;
  if (scenario == AutoSenseScenario::interruption ||
      scenario == AutoSenseScenario::interactive_interruption) {
    expected_status = interrupted_status;
  }
  auto expected_tail = baseline;
  append_bytes(expected_tail, *query);
  const bool recovered = failed || action == octa::AutoSenseAction::cancel;
  const bool raw_tail =
      io.outgoing.size() >= expected_tail.size() &&
      std::ranges::equal(std::span(io.outgoing).last(expected_tail.size()), expected_tail);
  const bool cancelled = scenario == AutoSenseScenario::interruption ||
                         scenario == AutoSenseScenario::interactive_interruption;
  const unsigned int connections = failed && !cancelled ? 2U : 1U;
  const bool recovery_verified =
      !failed || errors.str().contains("Original raw state restored and verified.");
  const bool removed_file = unlink(path.c_str()) == 0;
  const bool removed_directory = rmdir(directory.data()) == 0;
  return require(
      status == expected_status && saved && io.opens == connections &&
          io.closes == connections * 2 && (!recovered || raw_tail) && recovery_verified &&
          removed_file && removed_directory && bounded,
      "Auto-Sens workflow snapshots, completion, bounded result changes and raw recovery");
}

enum class RoutingScenario : std::uint8_t {
  success,
  mismatch,
  transfer_failure,
  interruption,
  unsupported_rate
};

bool routing_workflow(octa::cli::RoutingPreset preset, RoutingScenario scenario) {
  io = {};
  auto original = octa::decode(bulk_frame());
  const auto query = octa::request(octa::bulk_address, octa::bulk_size);
  if (!original || !query) { return false; }
  if (scenario == RoutingScenario::unsupported_rate) { original->payload[2] = 3; }
  const auto baseline = octa::encode(original->command, original->address, original->payload);
  if (!baseline) { return false; }
  io.replies = {*baseline};
  const std::array<std::uint8_t, 5> multi = {0, 1, 2, 3, 0};
  const std::array<std::uint8_t, 5> playback = {0, 5, 6, 7, 8};
  const auto &routes = preset == octa::cli::RoutingPreset::multi_monitor ? multi : playback;
  auto target = *original;
  const bool success = scenario == RoutingScenario::success;
  unsigned int steps = 2;
  if (success) { steps = 5; }
  if (scenario == RoutingScenario::transfer_failure) { io.fail_route = 3; }
  if (scenario == RoutingScenario::interruption) { io.interrupt_route = 2; }
  constexpr std::size_t low_cut_offset = 0x17;
  for (unsigned int pair = 0; pair < steps; ++pair) {
    target.payload[5 + pair] = routes[pair];
    if (scenario == RoutingScenario::mismatch && pair == 1) { target.payload[low_cut_offset] = 1; }
    const auto reply = octa::encode(target.command, target.address, target.payload);
    if (!reply) { return false; }
    io.replies.push_back(*reply);
  }
  io.replies.push_back(*baseline);
  auto directory = std::to_array("/tmp/octa-routing-test-XXXXXX");
  if (mkdtemp(directory.data()) == nullptr) { return false; }
  const auto path = std::string(directory.data()) + "/recovery.syx";
  std::stringbuf errors;
  auto *previous_errors = std::cerr.rdbuf(&errors);
  const int status =
      octa::cli::apply_routing(preset, {.port = "hw:fake", .snapshot_path = path.c_str()});
  std::cerr.rdbuf(previous_errors);
  std::ifstream file(path, std::ios::binary);
  std::array<std::uint8_t, octa::bulk_size + 14> bytes{};
  file.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  const bool saved = file.gcount() == static_cast<std::streamsize>(baseline->size()) &&
                     std::ranges::equal(std::span(bytes).first(baseline->size()), *baseline);
  const bool recovered = !success && scenario != RoutingScenario::unsupported_rate;
  const unsigned int connections = recovered ? 2U : 1U;
  const bool recovery_verified =
      !recovered || errors.str().contains("Original raw state restored and verified.");
  int expected_status = success ? 0 : 1;
  constexpr int interrupted_status = 130;
  if (scenario == RoutingScenario::interruption) { expected_status = interrupted_status; }
  const bool rate_guard = scenario != RoutingScenario::unsupported_rate || io.outgoing == *query;
  const bool removed_file = unlink(path.c_str()) == 0;
  const bool removed_directory = rmdir(directory.data()) == 0;
  return require(status == expected_status && saved && io.opens == connections &&
                     io.closes == connections * 2 && recovery_verified && rate_guard &&
                     removed_file && removed_directory,
                 "routing preset verified full state and recovery; 192 kHz writes refused");
}

bool direct_out_workflow(RoutingScenario scenario) {
  io = {};
  const auto initial = octa::decode(bulk_frame());
  const auto query = octa::request(octa::bulk_address, octa::bulk_size);
  if (!initial || !query) { return false; }
  auto original = octa::direct_out_target(*initial);
  if (!original) { return false; }
  constexpr std::size_t mono_offset = 0x60;
  constexpr std::size_t opaque_offset = 0x442;
  constexpr std::uint8_t all_bits = 127;
  original->payload[mono_offset] = all_bits;
  original->payload[opaque_offset] = all_bits;
  original->payload[9] = 8;
  if (scenario == RoutingScenario::unsupported_rate) { original->payload[2] = 3; }
  const auto baseline = octa::encode(original->command, original->address, original->payload);
  if (!baseline) { return false; }
  io.replies.assign(octa::direct_out_change_count + 1, *baseline);
  if (scenario == RoutingScenario::mismatch) {
    auto wrong = *original;
    constexpr std::size_t phantom_offset = 0x15;
    wrong.payload[phantom_offset] = 1;
    const auto reply = octa::encode(wrong.command, wrong.address, wrong.payload);
    if (!reply) { return false; }
    io.replies.back() = *reply;
  }
  io.replies.push_back(*baseline);
  if (scenario == RoutingScenario::transfer_failure) { io.fail_route = 3; }
  if (scenario == RoutingScenario::interruption) { io.interrupt_route = 2; }
  auto expected_wire = *query;
  for (const auto &change : octa::direct_out_changes()) {
    const auto frame = octa::encode_dsp_change(change);
    if (!frame) { return false; }
    append_bytes(expected_wire, *frame);
    append_bytes(expected_wire, *query);
  }
  auto directory = std::to_array("/tmp/octa-direct-out-test-XXXXXX");
  if (mkdtemp(directory.data()) == nullptr) { return false; }
  const auto path = std::string(directory.data()) + "/recovery.syx";
  std::stringbuf errors;
  auto *previous_errors = std::cerr.rdbuf(&errors);
  const int status =
      octa::cli::apply_direct_out({.port = "hw:fake", .snapshot_path = path.c_str()});
  std::cerr.rdbuf(previous_errors);
  std::ifstream file(path, std::ios::binary);
  std::array<std::uint8_t, octa::bulk_size + 14> bytes{};
  file.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  const bool saved = file.gcount() == static_cast<std::streamsize>(baseline->size()) &&
                     std::ranges::equal(std::span(bytes).first(baseline->size()), *baseline);
  const bool success = scenario == RoutingScenario::success;
  const bool recovered = !success && scenario != RoutingScenario::unsupported_rate;
  const unsigned int connections = recovered ? 2U : 1U;
  const bool recovery_verified =
      !recovered || errors.str().contains("Original raw state restored and verified.");
  int expected_status = success ? 0 : 1;
  constexpr int interrupted_status = 130;
  if (scenario == RoutingScenario::interruption) { expected_status = interrupted_status; }
  const bool exact_wire = !success || io.outgoing == expected_wire;
  const bool rate_guard = scenario != RoutingScenario::unsupported_rate || io.outgoing == *query;
  const bool removed_file = unlink(path.c_str()) == 0;
  const bool removed_directory = rmdir(directory.data()) == 0;
  return require(
      status == expected_status && saved && io.opens == connections &&
          io.closes == connections * 2 && exact_wire && rate_guard && recovery_verified &&
          removed_file && removed_directory,
      "Direct Out sequence/readbacks, full-state mismatch, partial I/O, signal and rate guard");
}

bool initialization_operations() {
  constexpr unsigned int byte_values = 256;
  for (unsigned int index = 0; index < byte_values; ++index) {
    io = {};
    auto reply = level_frame();
    append_bytes(reply, bulk_frame());
    io.replies = {reply};
    auto session = octa::MidiSession::open("hw:fake");
    if (!session) { return false; }
    const auto target = static_cast<octa::InitializationTarget>(index);
    bool busy = false;
    const auto result = session->initialize_settings(target, [&session, &busy](const auto &) {
      const auto reentrant = session->read_bulk_state();
      busy = !reentrant && reentrant.error().code == -EBUSY;
    });
    if (index >= 5) {
      if (!require(!result && result.error().code == -EINVAL && io.writes == 0,
                   "invalid initialization target performs no I/O")) {
        return false;
      }
      continue;
    }
    const auto frame = octa::encode_initialization(target);
    const auto query = octa::request(octa::bulk_address, octa::bulk_size);
    if (!frame || !query) { return false; }
    auto expected = *frame;
    append_bytes(expected, *query);
    if (!require(result && io.outgoing == expected && busy,
                 "all initialization sequences with fragmented transfers and fresh bulk query")) {
      return false;
    }
  }
  io = {};
  io.fail_write = 2;
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto failed = session->initialize_settings(octa::InitializationTarget::reverb);
  const auto retry = session->read_bulk_state();
  return require(!failed && failed.error().code == -EIO && !retry &&
                     retry.error().code == -EPROTO && io.writes == 2,
                 "partial initialization requires reopening; no blind retransmission");
}

enum class InitializationScenario : std::uint8_t {
  success,
  unrelated,
  unstable,
  transfer_failure,
  sigint,
  sigterm,
  rate
};

bool initialization_workflow(octa::InitializationTarget target, InitializationScenario scenario) {
  io = {};
  const auto initial = octa::decode(bulk_frame());
  if (!initial) { return false; }
  auto original = *initial;
  if (scenario == InitializationScenario::rate) { original.payload[2] = 3; }
  auto after = original;
  constexpr std::array<std::size_t, 5> example = {6, 0x1d, 0x83, 0x0a, 6};
  ++after.payload[example[static_cast<std::size_t>(target)]];
  if (scenario == InitializationScenario::unrelated) { after.payload[0] = 1; }
  const auto baseline = octa::encode(original.command, original.address, original.payload);
  const auto changed = octa::encode(after.command, after.address, after.payload);
  if (!baseline || !changed) { return false; }
  io.replies = {*baseline, *changed};
  if (scenario == InitializationScenario::unstable) {
    io.replies.push_back(*baseline);
  } else if (scenario != InitializationScenario::unrelated &&
             scenario != InitializationScenario::transfer_failure) {
    io.replies.push_back(*changed);
  }
  io.replies.push_back(*baseline);
  io.fail_after_initialization = scenario == InitializationScenario::transfer_failure;
  if (scenario == InitializationScenario::sigint) { io.initialization_signal = SIGINT; }
  if (scenario == InitializationScenario::sigterm) { io.initialization_signal = SIGTERM; }
  auto directory = std::to_array("/tmp/octa-initialization-test-XXXXXX");
  if (mkdtemp(directory.data()) == nullptr) { return false; }
  const auto path = std::string(directory.data()) + "/recovery.syx";
  std::stringbuf errors;
  auto *previous_errors = std::cerr.rdbuf(&errors);
  const int status =
      octa::cli::initialize_settings(target, {.port = "hw:fake", .snapshot_path = path.c_str()});
  std::cerr.rdbuf(previous_errors);
  std::ifstream file(path, std::ios::binary);
  std::array<std::uint8_t, octa::bulk_size + 14> bytes{};
  file.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  const bool saved = file.gcount() == static_cast<std::streamsize>(baseline->size()) &&
                     std::ranges::equal(std::span(bytes).first(baseline->size()), *baseline);
  const bool success =
      scenario == InitializationScenario::success || scenario == InitializationScenario::rate;
  const bool signal =
      scenario == InitializationScenario::sigint || scenario == InitializationScenario::sigterm;
  constexpr int interrupted_status = 130;
  const int normal_status = success ? 0 : 1;
  const int expected_status = signal ? interrupted_status : normal_status;
  const unsigned int connections = success ? 1U : 2U;
  const bool recovery_verified =
      success || errors.str().contains("Original raw state restored and verified.");
  const auto query = octa::request(octa::bulk_address, octa::bulk_size);
  const auto command = octa::encode_initialization(target);
  if (!query || !command) { return false; }
  auto expected_wire = *query;
  append_bytes(expected_wire, *command);
  append_bytes(expected_wire, *query);
  append_bytes(expected_wire, *query);
  const bool wire = !success || io.outgoing == expected_wire;
  const bool removed_file = unlink(path.c_str()) == 0;
  const bool removed_directory = rmdir(directory.data()) == 0;
  return require(status == expected_status && saved && io.opens == connections &&
                     io.closes == connections * 2 && recovery_verified && wire && removed_file &&
                     removed_directory,
                 "initialization backup, scope, stability, signals, recovery and 192 kHz support");
}

bool memory_operations() {
  io = {};
  auto reply = level_frame();
  append_bytes(reply, bulk_frame());
  io.replies = {reply, bulk_frame(1)};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  unsigned int notifications = 0;
  const auto stored = session->operate_memory(
      octa::MemoryAction::store, [&notifications](const octa::Message &) { ++notifications; });
  const auto loaded = session->operate_memory(octa::MemoryAction::load);
  constexpr std::array<std::uint8_t, 14> store = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 0, 0, 1, 0, 0x7f, 0xf7};
  constexpr std::array<std::uint8_t, 14> load = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 0, 0, 1, 1, 0x7e, 0xf7};
  const auto query = octa::request(octa::bulk_address, octa::bulk_size);
  if (!query) { return false; }
  std::vector<std::uint8_t> expected;
  append_bytes(expected, store);
  append_bytes(expected, *query);
  append_bytes(expected, load);
  append_bytes(expected, *query);
  return require(stored && loaded && stored->payload.front() == 0 && loaded->payload.front() == 1 &&
                     notifications == 1 && io.outgoing == expected,
                 "reference memory frames, fragmented transfer, notifications and fresh queries");
}

bool memory_rejection() {
  io = {};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  constexpr unsigned int byte_values = 256;
  for (unsigned int value = 2; value < byte_values; ++value) {
    const auto result = session->operate_memory(static_cast<octa::MemoryAction>(value));
    if (!require(!result && result.error().code == -EINVAL && io.writes == 0 && io.position == 0,
                 "invalid memory action rejected before MIDI I/O")) {
      return false;
    }
  }
  return true;
}

bool memory_partial_failure() {
  io = {};
  io.fail_write = 2;
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto failed = session->operate_memory(octa::MemoryAction::store);
  const auto writes = io.writes;
  const auto retry = session->operate_memory(octa::MemoryAction::load);
  return require(!failed && failed.error().code == -EIO && !retry &&
                     retry.error().code == -EPROTO && writes == 2 && io.writes == writes,
                 "partial memory transfer requires reopening rather than an unsafe retry");
}

bool memory_cli_case(octa::MemoryAction action, int interruption, bool fail_reply) {
  io = {};
  io.memory_signal = interruption;
  io.fail_after_memory = fail_reply;
  const auto baseline = bulk_frame();
  io.replies = {baseline, bulk_frame(1), baseline};
  auto directory = std::to_array("/tmp/octa-memory-test-XXXXXX");
  if (mkdtemp(directory.data()) == nullptr) { return false; }
  const auto path = std::string(directory.data()) + "/recovery.syx";
  const int result =
      octa::cli::operate_memory(action, {.port = "hw:fake", .snapshot_path = path.c_str()});
  std::ifstream file(path, std::ios::binary);
  std::array<std::uint8_t, octa::bulk_size + 14> bytes{};
  file.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  const bool identical = file.gcount() == static_cast<std::streamsize>(baseline.size()) &&
                         std::ranges::equal(std::span(bytes).first(baseline.size()), baseline);
  std::error_code metadata_error;
  const auto metadata = std::filesystem::status(path, metadata_error);
  const auto forbidden = std::filesystem::perms::group_all | std::filesystem::perms::others_all;
  const bool private_file =
      !metadata_error && (metadata.permissions() & forbidden) == std::filesystem::perms::none;
  const bool failed = interruption != 0 || fail_reply;
  const bool recovered = failed && action == octa::MemoryAction::load;
  const unsigned int connections = recovered ? 2U : 1U;
  constexpr int interrupted_status = 130;
  const int failure_status = fail_reply ? 1 : 0;
  const int expected = interruption != 0 ? interrupted_status : failure_status;
  const auto query = octa::request(octa::bulk_address, octa::bulk_size);
  const std::array payload = {static_cast<std::uint8_t>(action)};
  const auto command = octa::encode(octa::Command::data, {0, 0, 0, 1}, payload);
  std::vector<std::uint8_t> expected_wire;
  if (query && command) {
    append_bytes(expected_wire, *query);
    append_bytes(expected_wire, *command);
    append_bytes(expected_wire, *query);
    if (recovered) {
      append_bytes(expected_wire, baseline);
      append_bytes(expected_wire, *query);
    }
  }
  const bool exact_transfer = query && command && io.outgoing == expected_wire;
  const bool removed_file = unlink(path.c_str()) == 0;
  const bool removed_directory = rmdir(directory.data()) == 0;
  return require(result == expected && identical && private_file && exact_transfer &&
                     io.opens == connections && io.closes == io.opens * 2 && removed_file &&
                     removed_directory,
                 "memory CLI private snapshot and interruption/error recovery");
}

bool memory_cli_guard(octa::MemoryAction action) {
  io = {};
  const auto baseline = bulk_frame();
  io.replies = {baseline, baseline};
  auto directory = std::to_array("/tmp/octa-memory-guard-XXXXXX");
  if (mkdtemp(directory.data()) == nullptr) { return false; }
  const auto path = std::string(directory.data()) + "/recovery.syx";
  {
    std::ofstream file(path);
    file << "preserve";
  }
  const int collision =
      octa::cli::operate_memory(action, {.port = "hw:fake", .snapshot_path = path.c_str()});
  std::ifstream original(path);
  std::string contents;
  original >> contents;
  const auto impossible = std::string(directory.data()) + "/missing/recovery.syx";
  const int creation_failed =
      octa::cli::operate_memory(action, {.port = "hw:fake", .snapshot_path = impossible.c_str()});
  const auto query = octa::request(octa::bulk_address, octa::bulk_size);
  std::vector<std::uint8_t> expected;
  if (query) {
    append_bytes(expected, *query);
    append_bytes(expected, *query);
  }
  const bool removed_file = unlink(path.c_str()) == 0;
  const bool removed_directory = rmdir(directory.data()) == 0;
  return require(
      collision == 1 && creation_failed == 1 && contents == "preserve" && query &&
          io.outgoing == expected && io.opens == 2 && io.closes == 4 && removed_file &&
          removed_directory,
      "memory backup collision and creation failure never send a state-changing command");
}

bool preset_transfer() {
  io = {};
  constexpr std::size_t whole_chunk = 512;
  io.read_size = whole_chunk;
  auto original = octa::decode(bulk_frame());
  if (!original) { return false; }
  constexpr std::size_t mono = 0x60;
  constexpr std::size_t unmapped_flag = 0x442;
  original->payload[mono] = 1;
  original->payload[unmapped_flag] = 1;
  const auto restored_frame = octa::encode(original->command, original->address, original->payload);
  const auto target = octa::preset_to_bulk({}, *original);
  if (!restored_frame || !target) { return false; }
  const auto imported_frame = octa::encode(target->command, target->address, target->payload);
  if (!imported_frame) { return false; }
  auto reply = level_frame();
  append_bytes(reply, *imported_frame);
  io.replies = {reply, *restored_frame};
  unsigned int notifications = 0;
  {
    auto session = octa::MidiSession::open("hw:fake");
    if (!session || !session->set_input_meters(true)) { return false; }
    const auto imported =
        session->write_preset({}, *original, [&](const octa::Message &) { ++notifications; });
    const auto restored = session->write_bulk_state(*original);
    if (!require(imported && *imported == *target && restored && *restored == *original &&
                     notifications == 1 && io.opens == 1,
                 "full preset write/readback while meters run, then raw recovery preserves omitted "
                 "flags")) {
      return false;
    }
  }
  const auto request = octa::request(octa::bulk_address, octa::bulk_size);
  if (!request) { return false; }
  auto expected = switch_frame(true);
  append_bytes(expected, *imported_frame);
  append_bytes(expected, *request);
  append_bytes(expected, *restored_frame);
  append_bytes(expected, *request);
  append_bytes(expected, switch_frame(false));
  return require(io.outgoing == expected && io.closes == 2,
                 "each complete bulk write is followed by a fresh RQ1 on the same connection");
}

bool preset_rejection() {
  io = {};
  io.incoming = level_frame();
  auto original = octa::decode(bulk_frame());
  auto session = octa::MidiSession::open("hw:fake");
  if (!original || !session) { return false; }
  octa::Preset invalid{};
  invalid.output_routes.front() = 9;
  const auto profile = session->write_preset(invalid, *original);
  constexpr std::size_t first_volume = 0x120;
  original->payload[first_volume] = 8; // 0x800000 is above the known write maximum.
  const auto raw = session->write_bulk_state(*original);
  return require(!profile && profile.error().code == -EINVAL && !raw &&
                     raw.error().code == -EINVAL && io.writes == 0 && io.position == 0,
                 "invalid full-state values rejected before any MIDI read/write or listener call");
}

bool malformed_preset_bulk() {
  io = {};
  auto malformed = octa::decode(bulk_frame());
  if (!malformed) { return false; }
  constexpr std::size_t sensitivity_offset = 0x1d;
  constexpr std::size_t packed_pan_offset = 0xf2;
  constexpr std::uint8_t sensitivity = 96;
  constexpr std::uint8_t invalid_nibble = 32;
  malformed->payload[sensitivity_offset] = sensitivity;
  malformed->payload[packed_pan_offset] = invalid_nibble;
  const auto frame = octa::encode(malformed->command, malformed->address, malformed->payload);
  if (!frame) { return false; }
  io.replies = {*frame};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto received = session->read_bulk_state();
  if (!received) { return false; }
  const auto writes = io.writes;
  const auto preset = octa::preset_from_bulk(*received);
  const auto restored = session->write_bulk_state(*received);
  return require(
      *received == *malformed && !preset && preset.error() == octa::PresetStateError::data &&
          !restored && restored.error().code == -EINVAL && io.writes == writes,
      "checksum-valid malformed DSP data stays readable but cannot be exported or written");
}

struct PresetReadbackCase {
  const octa::Message &original;
  const octa::Message &raw_target;
  const octa::Message &reply;
  bool accepted;
  bool raw;
};

bool preset_readback_case(const PresetReadbackCase &test) {
  io = {};
  const auto encoded = octa::encode(test.reply.command, test.reply.address, test.reply.payload);
  if (!encoded) { return false; }
  io.replies = {*encoded};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  auto sent = octa::preset_to_bulk({}, test.original);
  if (test.raw && sent) { *sent = test.raw_target; }
  if (!sent) { return false; }
  auto expected_wire = octa::encode(sent->command, sent->address, sent->payload);
  const auto request = octa::request(octa::bulk_address, octa::bulk_size);
  if (!expected_wire || !request) { return false; }
  append_bytes(*expected_wire, *request);
  const auto result = test.raw ? session->write_bulk_state(test.raw_target)
                               : session->write_preset({}, test.original);
  const bool outcome =
      test.accepted ? result && *result == test.reply : !result && result.error().code == -EIO;
  return require(
      outcome && io.outgoing == *expected_wire,
      "192 kHz preset permits only commanded/retained metadata; raw recovery stays exact");
}

bool preset_192_metadata_readback() {
  constexpr std::size_t rate_offset = 2;
  constexpr std::size_t metadata_offset = 4;
  constexpr std::uint8_t highest_rate = 3;
  constexpr std::uint8_t retained_metadata = 1;
  auto original = octa::decode(bulk_frame());
  if (!original) { return false; }
  original->payload[rate_offset] = highest_rate;
  original->payload[metadata_offset] = retained_metadata;
  const auto target = octa::preset_to_bulk({}, *original);
  if (!target || !require(target->payload[metadata_offset] == 0,
                          "192 kHz preset wire prefix still matches the original transmitter")) {
    return false;
  }
  auto exercise = [&](const octa::Message &reply, bool accepted, bool raw) {
    return preset_readback_case({.original = *original,
                                 .raw_target = *target,
                                 .reply = reply,
                                 .accepted = accepted,
                                 .raw = raw});
  };
  auto reply = *target;
  if (!exercise(reply, true, false)) { return false; }
  reply.payload[metadata_offset] = retained_metadata;
  if (!exercise(reply, true, false) || !exercise(reply, false, true)) { return false; }
  constexpr std::uint8_t unexpected_metadata = 7;
  reply.payload[metadata_offset] = unexpected_metadata;
  if (!exercise(reply, false, false)) { return false; }
  constexpr std::array<std::size_t, 10> other_fields = {
      0, 1, 2, 3, 5, 0x1d, 0x80, 0x120, 0x442, octa::bulk_size - 1};
  for (const auto offset : other_fields) {
    reply = *target;
    reply.payload[metadata_offset] = retained_metadata;
    reply.payload[offset] ^= 1;
    if (!exercise(reply, false, false)) { return false; }
  }
  original->payload[rate_offset] = 0;
  reply = *target;
  reply.payload[rate_offset] = 0;
  return exercise(reply, false, false);
}

bool preset_readback_failure() {
  io = {};
  io.replies = {bulk_frame(), bulk_frame()};
  const auto original = octa::decode(bulk_frame());
  auto session = octa::MidiSession::open("hw:fake");
  if (!original || !session) { return false; }
  octa::Preset changed{};
  changed.mixers.front().input.front().channels.front().pan = 1;
  const auto mismatch = session->write_preset(changed, *original);
  const auto restored = session->write_bulk_state(*original);
  return require(!mismatch && mismatch.error().code == -EIO &&
                     mismatch.error().operation == "bulk readback" && restored &&
                     *restored == *original && io.opens == 1,
                 "complete bulk mismatch is an error, and a verified recovery can follow");
}

bool preset_partial_failure() {
  io = {};
  io.fail_write = 2;
  const auto original = octa::decode(bulk_frame());
  if (!original) { return false; }
  {
    auto session = octa::MidiSession::open("hw:fake");
    if (!session) { return false; }
    const auto failed = session->write_bulk_state(*original);
    const auto writes = io.writes;
    const auto blocked = session->write_bulk_state(*original);
    if (!require(
            !failed && failed.error().code == -EIO && !blocked && blocked.error().code == -EPROTO &&
                io.writes == writes,
            "partial bulk write fails and prevents another transaction on the uncertain session")) {
      return false;
    }
  }
  io.fail_write = 0;
  io.replies = {bulk_frame()};
  {
    auto reopened = octa::MidiSession::open("hw:fake");
    if (!reopened) { return false; }
    const auto recovered = reopened->write_bulk_state(*original);
    if (!require(recovered && *recovered == *original,
                 "a new session recovers after a partial SysEx prefix using a complete frame")) {
      return false;
    }
  }
  return require(io.opens == 2 && io.closes == 4, "failed and recovery handles are all closed");
}

bool session_uncertain() {
  io = {};
  auto malformed = bulk_frame();
  malformed[malformed.size() - 2] ^= 1;
  io.replies = {malformed};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  const auto failed = session->read_bulk_state();
  const auto before = io.writes;
  const auto retry = session->read_bulk_state();
  const auto start_auto = session->start_auto_sense(1);
  const bool no_retry = io.writes == before;
  const auto cancel_auto = session->stop_auto_sense(octa::AutoSenseAction::cancel);
  const auto stop = session->set_input_meters(false);
  return require(!failed && failed.error().code == -EBADMSG && !retry &&
                     retry.error().code == -EPROTO && !start_auto &&
                     start_auto.error().code == -EPROTO && no_retry && cancel_auto && stop,
                 "unresolved query requires reopening, while meter disable remains available");
}

bool session_receive_bounds() {
  io = {};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  constexpr std::array<std::int64_t, 3> invalid = {-1, 0, 60001};
  for (const auto timeout : invalid) {
    const auto result = session->receive(std::chrono::milliseconds(timeout));
    if (!require(!result && result.error().code == -EINVAL && io.position == 0,
                 "invalid receive timeouts rejected without reading")) {
      return false;
    }
  }
  return true;
}

bool impossible_transfers() {
  io = {};
  auto session = octa::MidiSession::open("hw:fake");
  if (!session) { return false; }
  io.oversized_read = true;
  const auto read = session->receive(std::chrono::seconds(1));
  io.oversized_read = false;
  io.oversized_write = true;
  const auto write = session->set_input_meters(true);
  io.oversized_write = false;
  const auto stop = session->set_input_meters(false);
  return require(!read && read.error().code == -EIO && read.error().operation == "MIDI read size" &&
                     !write && write.error().code == -EIO &&
                     write.error().operation == "MIDI write size" && stop,
                 "impossible transfer sizes are rejected before forming a subspan");
}

bool open_failure() {
  io = {};
  io.open_error = -EACCES;
  const auto result = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [](const octa::InputMeters &) { return false; });
  return require(!result && result.error().code == -EACCES && io.writes == 0 && io.closes == 0,
                 "failed open never starts the meter stream");
}

int handle_fake_route(const octa::Message &message) {
  if (message.address[0] != 0 || message.address[1] != 3 || message.address[2] != 0) { return 0; }
  ++io.route_commands;
  if (io.route_commands == io.fail_route) { io.fail_write = io.writes + 2; }
  if (io.route_commands == io.interrupt_route && kill(getpid(), SIGINT) != 0) { return -errno; }
  return 0;
}

int handle_fake_auto_sense(const octa::Message &message) {
  if (message.address == octa::Address{0, 2, 1, 4}) {
    append_bytes(io.incoming, io.auto_start_reply);
    if (io.auto_fail_stop) { io.fail_write = io.writes + 2; }
    if (io.auto_signal != 0 && kill(getpid(), io.auto_signal) != 0) { return -errno; }
  }
  if (message.address == octa::Address{0, 2, 1, 5}) {
    if (io.reject_observed_stop) { return -EIO; }
    if (io.require_auto_completion) {
      io.auto_stop_pending = io.auto_stop_reply;
      io.auto_completion_end = io.incoming.size() + io.auto_stop_pending.size();
    } else {
      append_bytes(io.incoming, io.auto_stop_reply);
    }
  }
  return 0;
}

int handle_fake_data(const octa::Message &message) {
  if (message.command != octa::Command::data) { return 0; }
  if (message.address == octa::bulk_address && io.require_auto_completion &&
      io.position < io.auto_completion_end) {
    return -EIO;
  }
  if (message.address == octa::Address{0, 0, 0, 1}) {
    if (io.fail_after_memory) { ++io.read_failures; }
    if (io.memory_signal != 0 && kill(getpid(), io.memory_signal) != 0) { return -errno; }
  }
  if (message.address == octa::Address{0, 0, 0, 2}) {
    if (io.fail_after_initialization) { ++io.read_failures; }
    if (io.initialization_signal != 0 && kill(getpid(), io.initialization_signal) != 0) {
      return -errno;
    }
  }
  const int status = handle_fake_auto_sense(message);
  if (status != 0) { return status; }
  return handle_fake_route(message);
}

void handle_fake_request(const octa::Message &message) {
  if (message.command == octa::Command::request && io.reply_position < io.replies.size()) {
    append_bytes(io.incoming, io.replies[io.reply_position++]);
  }
}

}

// Test-only ALSA symbol implementations interpose the dynamically linked library.
// No hardware handle is opened. The real poll uses a pipe for readiness/timeouts.
extern "C" int snd_rawmidi_open(snd_rawmidi_t **in_rmidi,
                                snd_rawmidi_t **out_rmidi,
                                const char * /*name*/,
                                int /*mode*/) {
  if (io.open_error != 0) { return io.open_error; }
  ++io.opens;
  const std::array handles{in_rmidi, out_rmidi};
  *handles.front() = reinterpret_cast<snd_rawmidi_t *>(&input_token);
  *handles.back() = reinterpret_cast<snd_rawmidi_t *>(&output_token);
  return 0;
}

extern "C" int snd_rawmidi_close(snd_rawmidi_t * /*rmidi*/) {
  ++io.closes;
  return 0;
}

extern "C" ssize_t snd_rawmidi_read(snd_rawmidi_t * /*rmidi*/, void *buffer, std::size_t size) {
  if (io.read_error != 0) { return io.read_error; }
  if (io.read_failures != 0) {
    --io.read_failures;
    return -EIO;
  }
  if (io.oversized_read) { return static_cast<ssize_t>(size + 1); }
  if (io.position == io.incoming.size()) { return -EAGAIN; }
  const auto count = std::min({size, io.read_size, io.incoming.size() - io.position});
  std::ranges::copy(std::span(io.incoming).subspan(io.position, count),
                    static_cast<std::uint8_t *>(buffer));
  io.position += count;
  return static_cast<ssize_t>(count);
}

extern "C" ssize_t
snd_rawmidi_write(snd_rawmidi_t * /*rmidi*/, const void *buffer, std::size_t size) {
  ++io.writes;
  if (io.oversized_write) { return static_cast<ssize_t>(size + 1); }
  if (io.writes == io.fail_write) { return -EIO; }
  const auto count = std::min(size, io.write_size);
  const std::span bytes(static_cast<const std::uint8_t *>(buffer), count);
  io.outgoing.insert(io.outgoing.end(), bytes.begin(), bytes.end());
  for (const auto byte : bytes) {
    const auto result = io.requests.feed(byte);
    if (!result) { continue; }
    const auto &message = *result;
    if (!message.has_value()) { continue; }
    const auto status = handle_fake_data(*message);
    if (status != 0) { return status; }
    handle_fake_request(*message);
  }
  return static_cast<ssize_t>(count);
}

extern "C" int snd_rawmidi_poll_descriptors_count(snd_rawmidi_t * /*rmidi*/) {
  // A nonblocking drain cannot observe this delayed cancellation reply.
  if (!io.auto_stop_pending.empty()) {
    append_bytes(io.incoming, io.auto_stop_pending);
    io.auto_stop_pending.clear();
    constexpr char ready = 1;
    if (write(wake_pipe.back(), &ready, 1) != 1) { return -EIO; }
  }
  return 1;
}

extern "C" int
snd_rawmidi_poll_descriptors(snd_rawmidi_t * /*rmidi*/, struct pollfd *pfds, unsigned int space) {
  if (space != 1) { return -EINVAL; }
  *pfds = {.fd = wake_pipe.front(), .events = POLLIN, .revents = 0};
  return 1;
}

extern "C" int snd_rawmidi_poll_descriptors_revents(snd_rawmidi_t * /*rawmidi*/,
                                                    struct pollfd *pfds,
                                                    unsigned int nfds,
                                                    unsigned short *revent) {
  if (nfds != 1) { return -EINVAL; }
  *revent = static_cast<unsigned short>(pfds->revents);
  if ((pfds->revents & POLLIN) != 0) {
    char ready = 0;
    if (read(wake_pipe.front(), &ready, 1) != 1) { return -EIO; }
  }
  return 0;
}

namespace {

int observed_cases() {
  bool passed = true;
  for (const auto action : {octa::AutoSenseAction::finish, octa::AutoSenseAction::cancel}) {
    for (const auto scenario : {ObservedScenario::active,
                                ObservedScenario::unknown,
                                ObservedScenario::inactive,
                                ObservedScenario::already_ended,
                                ObservedScenario::reactivated,
                                ObservedScenario::missing_end,
                                ObservedScenario::malformed_end,
                                ObservedScenario::invalid_initial,
                                ObservedScenario::invalid_final,
                                ObservedScenario::restarted,
                                ObservedScenario::write_failure}) {
      passed = observed_auto_sense(action, scenario) && passed;
    }
  }
  const int read_closed = close(wake_pipe.front());
  const int write_closed = close(wake_pipe.back());
  return passed && read_closed == 0 && write_closed == 0 ? 0 : 1;
}

int transport_cases() {
  bool memory_cli_passed = true;
  for (const auto action : {octa::MemoryAction::store, octa::MemoryAction::load}) {
    memory_cli_passed = memory_cli_guard(action) && memory_cli_passed;
    memory_cli_passed = memory_cli_case(action, 0, false) && memory_cli_passed;
    memory_cli_passed = memory_cli_case(action, 0, true) && memory_cli_passed;
    memory_cli_passed = memory_cli_case(action, SIGINT, false) && memory_cli_passed;
    memory_cli_passed = memory_cli_case(action, SIGTERM, false) && memory_cli_passed;
  }
  bool auto_sense_workflow_passed = true;
  for (const auto action : {octa::AutoSenseAction::finish, octa::AutoSenseAction::cancel}) {
    for (const auto scenario : {AutoSenseScenario::success,
                                AutoSenseScenario::linked_success,
                                AutoSenseScenario::unselected_peer,
                                AutoSenseScenario::malformed,
                                AutoSenseScenario::missing_start,
                                AutoSenseScenario::missing_completion,
                                AutoSenseScenario::unrelated_result,
                                AutoSenseScenario::partial_stop,
                                AutoSenseScenario::interruption,
                                AutoSenseScenario::interactive_finish,
                                AutoSenseScenario::interactive_missing_completion,
                                AutoSenseScenario::interactive_interruption}) {
      auto_sense_workflow_passed =
          auto_sense_workflow(action, scenario) && auto_sense_workflow_passed;
    }
  }
  bool routing_passed = true;
  for (const auto preset :
       {octa::cli::RoutingPreset::multi_monitor, octa::cli::RoutingPreset::playback}) {
    for (const auto scenario : {RoutingScenario::success,
                                RoutingScenario::mismatch,
                                RoutingScenario::transfer_failure,
                                RoutingScenario::interruption,
                                RoutingScenario::unsupported_rate}) {
      routing_passed = routing_workflow(preset, scenario) && routing_passed;
    }
  }
  bool direct_out_passed = true;
  for (const auto scenario : {RoutingScenario::success,
                              RoutingScenario::mismatch,
                              RoutingScenario::transfer_failure,
                              RoutingScenario::interruption,
                              RoutingScenario::unsupported_rate}) {
    direct_out_passed = direct_out_workflow(scenario) && direct_out_passed;
  }
  bool initialization_passed = initialization_operations();
  for (const auto target : {octa::InitializationTarget::all,
                            octa::InitializationTarget::preamps,
                            octa::InitializationTarget::direct_mix,
                            octa::InitializationTarget::reverb,
                            octa::InitializationTarget::patchbay}) {
    for (const auto scenario : {InitializationScenario::success,
                                InitializationScenario::unrelated,
                                InitializationScenario::unstable,
                                InitializationScenario::transfer_failure,
                                InitializationScenario::sigint,
                                InitializationScenario::sigterm,
                                InitializationScenario::rate}) {
      initialization_passed = initialization_workflow(target, scenario) && initialization_passed;
    }
  }
  const bool passed =
      initialization_passed && direct_out_passed && routing_passed && memory_cli_passed &&
      auto_sense_workflow_passed && successful_stream() && input_failure(false) &&
      input_failure(true) && write_failures() && deadlines() && open_failure() &&
      auto_sense_session() && auto_sense_cleanup() && auto_sense_failures(true) &&
      auto_sense_failures(false) && auto_sense_rejection() && session_buffering() &&
      session_queries() && session_stale_fragment() && session_writes() &&
      input_monitor_session(MonitorScenario::success) &&
      input_monitor_session(MonitorScenario::highest_rate) &&
      input_monitor_session(MonitorScenario::unrelated_change) &&
      input_monitor_session(MonitorScenario::inactive_input) &&
      input_monitor_session(MonitorScenario::unmapped_input) && memory_operations() &&
      memory_rejection() && memory_partial_failure() && preset_transfer() && preset_rejection() &&
      malformed_preset_bulk() && preset_192_metadata_readback() && preset_readback_failure() &&
      preset_partial_failure() && session_uncertain() && session_receive_bounds() &&
      impossible_transfers() && panel_operations() && panel_rejection() && panel_failures();
  const int read_closed = close(wake_pipe.front());
  const int write_closed = close(wake_pipe.back());
  return passed && read_closed == 0 && write_closed == 0 ? 0 : 1;
}

}

int main(int argc, char **argv) {
  if (argc != 1 && (argc != 2 || std::string_view(argv[1]) != "--observed-auto-sense")) {
    return 2;
  }
  if (pipe(wake_pipe.data()) != 0) { return 1; }
  return argc == 2 ? observed_cases() : transport_cases();
}
