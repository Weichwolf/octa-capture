// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PairPcm.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <cstddef>
#include <iostream>
#include <string_view>
#include <utility>
#include <vector>

namespace {
constexpr std::array<std::string_view, 2> names = {"BASE", "EXP"};

struct Metadata {
  int card;
  unsigned int device = 0;
  unsigned int subdevice = 0;
};

struct Peer {
  std::array<Metadata, 4> metadata = {
      Metadata{.card = 0}, Metadata{.card = 0}, Metadata{.card = 1}, Metadata{.card = 1}};
  std::array<bool, 4> active{};
  std::array<snd_pcm_state_t, 4> states{};
  unsigned int opens = 0;
  unsigned int metadata_allocations = 0;
  unsigned int metadata_frees = 0;
  unsigned int fail_open = 0;
  unsigned int fail_allocate = 0;
  unsigned int null_allocate = 0;
  unsigned int fail_info = 0;
  int open_error = -EBUSY;
  std::size_t info_slot = 0;
  bool metadata_live = false;
  bool invalid = false;
  std::vector<std::size_t> closed;
};

Peer io;
std::array<std::max_align_t, 4> pcm_tokens{};
std::max_align_t info_token{};

snd_pcm_t *handle(std::size_t index) {
  return reinterpret_cast<snd_pcm_t *>(&pcm_tokens[index]);
}

bool require(bool condition, std::string_view message) {
  if (!condition) { std::cerr << "FAIL " << message << '\n'; }
  return condition;
}

bool clean(unsigned int count) {
  std::vector<std::size_t> expected;
  for (std::size_t index = count; index > 0; --index) { expected.push_back(index - 1); }
  return require(!io.invalid && !io.metadata_live && io.closed == expected &&
                     std::ranges::none_of(io.active, [](bool active) { return active; }),
                 "every acquired PCM closes once in reverse order");
}

bool successful_move() {
  io = {};
  {
    auto pair = octa::PairPcmReservation::open(names);
    if (!require(pair && pair->card_indices() == std::array{0, 1} && io.opens == 4 &&
                     io.metadata_allocations == 4 && io.metadata_frees == 4 && io.closed.empty(),
                 "four native PCMs and their card metadata are reserved before return")) {
      return false;
    }
    auto moved = std::move(*pair);
    if (!require(moved.capture(octa::PairUnit::base) == handle(1) &&
                     moved.capture(octa::PairUnit::expansion) == handle(3) &&
                     pair->capture(octa::PairUnit::base) == nullptr &&
                     pair->capture(octa::PairUnit::expansion) == nullptr,
                 "move transfers ownership of both capture selectors")) {
      return false;
    }
  }
  return clean(4);
}

bool invalid_names() {
  constexpr auto embedded = std::to_array("a\0b");
  const std::array<std::string_view, 7> invalid = {
      "",
      "hw:0",
      "a/b",
      "a\"b",
      "a b",
      "1234567890123456",
      std::string_view(embedded.data(), embedded.size() - 1)};
  for (const auto name : invalid) {
    for (std::size_t index = 0; index < 2; ++index) {
      io = {};
      auto cards = names;
      cards[index] = name;
      const auto pair = octa::PairPcmReservation::open(cards);
      if (!require(!pair && pair.error().code == -EINVAL && io.opens == 0,
                   "invalid card identifiers fail before ALSA")) {
        return false;
      }
    }
  }
  io = {};
  const auto duplicate = octa::PairPcmReservation::open({"BASE", "BASE"});
  return require(!duplicate && duplicate.error().code == -EINVAL && io.opens == 0,
                 "duplicate selectors fail before ALSA");
}

bool disconnected_directions() {
  for (std::size_t index = 0; index < 4; ++index) {
    io = {};
    {
      auto pair = octa::PairPcmReservation::open(names);
      if (!pair || !pair->check(octa::PairUnit::base) || !pair->check(octa::PairUnit::expansion)) {
        return false;
      }
      io.states[index] = SND_PCM_STATE_DISCONNECTED;
      const auto failed = pair->check(index < 2 ? octa::PairUnit::base : octa::PairUnit::expansion);
      const auto other = pair->check(index < 2 ? octa::PairUnit::expansion : octa::PairUnit::base);
      if (!require(
              !failed && failed.error().code == -ENODEV && other.has_value(),
              "disconnect of either direction invalidates that unit while preserving the other")) {
        return false;
      }
    }
    if (!clean(4)) { return false; }
  }
  return true;
}

bool open_failures() {
  for (unsigned int failed = 1; failed <= 4; ++failed) {
    for (const auto error : {-EBUSY, -ENODEV, -EACCES, -ENOMEM}) {
      io = {};
      io.fail_open = failed;
      io.open_error = error;
      const auto pair = octa::PairPcmReservation::open(names);
      if (!require(!pair && pair.error().code == error && io.opens == failed,
                   "failed direction preserves ALSA error and stops acquisition") ||
          !clean(failed - 1)) {
        return false;
      }
    }
  }
  return true;
}

bool retained_metadata() {
  io = {};
  {
    auto pair = octa::PairPcmReservation::open(names);
    if (!pair) { return false; }
    io.fail_info = io.opens;
    const auto removed = pair->check(octa::PairUnit::base);
    io.fail_info = 0;
    io.metadata[1].card = 2;
    const auto replaced = pair->check(octa::PairUnit::base);
    io.metadata[1].card = 0;
    const auto recovered = pair->check(octa::PairUnit::base);
    if (!require(!removed && removed.error().code == -ENODEV && !replaced &&
                     replaced.error().code == -EXDEV && recovered.has_value(),
                 "retained descriptors must still answer metadata queries for the original card")) {
      return false;
    }
  }
  return clean(4);
}

bool metadata_failures() {
  struct Fault {
    unsigned int Peer::*field;
    int error;
    unsigned int unallocated;
  };

  constexpr std::array faults = {
      Fault{.field = &Peer::fail_allocate, .error = -ENOMEM, .unallocated = 1},
      Fault{.field = &Peer::null_allocate, .error = -EIO, .unallocated = 1},
      Fault{.field = &Peer::fail_info, .error = -ENODEV, .unallocated = 0}};
  for (unsigned int failed = 1; failed <= 4; ++failed) {
    for (const auto &fault : faults) {
      io = {};
      io.*fault.field = failed;
      const auto pair = octa::PairPcmReservation::open(names);
      if (!require(!pair && pair.error().code == fault.error && io.opens == failed &&
                       io.metadata_frees == failed - fault.unallocated,
                   "metadata failures release the current handle and metadata") ||
          !clean(failed)) {
        return false;
      }
    }
  }
  return true;
}

bool metadata_guards() {
  constexpr std::array invalid = {Metadata{.card = -1},
                                  Metadata{.card = 256},
                                  Metadata{.card = 0, .device = 1},
                                  Metadata{.card = 0, .subdevice = 1}};
  for (std::size_t slot = 0; slot < 4; ++slot) {
    for (const auto &metadata : invalid) {
      io = {};
      io.metadata[slot] = metadata;
      const auto pair = octa::PairPcmReservation::open(names);
      if (!require(!pair && pair.error().code == -EPROTO,
                   "reject non-native or invalid PCM metadata") ||
          !clean(static_cast<unsigned int>(slot + 1))) {
        return false;
      }
    }
  }
  for (const auto slot : {1U, 2U, 3U}) {
    io = {};
    io.metadata[slot].card = slot == 2 ? 0 : 2;
    const auto pair = octa::PairPcmReservation::open(names);
    if (!require(!pair && pair.error().code == -EXDEV,
                 "reject aliases of one card and mismatched capture/playback identities") ||
        !clean(slot + 1)) {
      return false;
    }
  }
  return true;
}
}

extern "C" int snd_pcm_open(snd_pcm_t **pcm, const char *name, snd_pcm_stream_t stream, int mode) {
  const auto index = io.opens++;
  if (index >= 4) {
    io.invalid = true;
    return -EINVAL;
  }
  const char *const expected =
      index < 2 ? "hw:CARD=BASE,DEV=0,SUBDEV=0" : "hw:CARD=EXP,DEV=0,SUBDEV=0";
  if (std::string_view(name) != expected || mode != SND_PCM_NONBLOCK ||
      stream != (index % 2 == 0 ? SND_PCM_STREAM_PLAYBACK : SND_PCM_STREAM_CAPTURE)) {
    io.invalid = true;
    return -EINVAL;
  }
  if (io.opens == io.fail_open) { return io.open_error; }
  *pcm = handle(index);
  io.active[index] = true;
  return 0;
}

extern "C" int snd_pcm_close(snd_pcm_t *pcm) {
  for (std::size_t index = 0; index < 4; ++index) {
    if (pcm != handle(index)) { continue; }
    if (!io.active[index]) { io.invalid = true; }
    io.active[index] = false;
    io.closed.push_back(index);
    return 0;
  }
  io.invalid = true;
  return -EINVAL;
}

extern "C" snd_pcm_state_t snd_pcm_state(snd_pcm_t *pcm) {
  for (std::size_t index = 0; index < 4; ++index) {
    if (pcm == handle(index) && io.active[index]) { return io.states[index]; }
  }
  io.invalid = true;
  return SND_PCM_STATE_DISCONNECTED;
}

extern "C" int snd_pcm_info_malloc(snd_pcm_info_t **ptr) {
  ++io.metadata_allocations;
  if (io.metadata_allocations == io.fail_allocate) { return -ENOMEM; }
  if (io.metadata_allocations == io.null_allocate) { return 0; }
  if (io.metadata_live) { io.invalid = true; }
  io.metadata_live = true;
  *ptr = reinterpret_cast<snd_pcm_info_t *>(&info_token);
  return 0;
}

extern "C" void snd_pcm_info_free(snd_pcm_info_t *obj) {
  if (obj != reinterpret_cast<snd_pcm_info_t *>(&info_token) || !io.metadata_live) {
    io.invalid = true;
  }
  io.metadata_live = false;
  ++io.metadata_frees;
}

extern "C" int snd_pcm_info(snd_pcm_t *pcm, snd_pcm_info_t * /*info*/) {
  for (std::size_t index = 0; index < 4; ++index) {
    if (pcm == handle(index) && io.active[index]) {
      io.info_slot = index;
      return io.opens == io.fail_info ? -ENODEV : 0;
    }
  }
  io.invalid = true;
  return -EINVAL;
}

extern "C" int snd_pcm_info_get_card(const snd_pcm_info_t * /*info*/) {
  return io.metadata[io.info_slot].card;
}

extern "C" unsigned int snd_pcm_info_get_device(const snd_pcm_info_t * /*info*/) {
  return io.metadata[io.info_slot].device;
}

extern "C" unsigned int snd_pcm_info_get_subdevice(const snd_pcm_info_t * /*info*/) {
  return io.metadata[io.info_slot].subdevice;
}

int main() {
  if (!successful_move() || !invalid_names() || !open_failures() || !metadata_failures() ||
      !metadata_guards() || !disconnected_directions() || !retained_metadata()) {
    return 1;
  }
  std::cout << "PASS paired native PCM ownership, metadata, move and partial-failure cleanup; no "
               "hardware access\n";
}
