// SPDX-License-Identifier: GPL-2.0-only
// Fixed libusb replies exercise the probe without opening any real USB device.
#include "octa/Usb.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <libusb.h>
#include <string_view>
#include <sys/types.h>
#include <tuple>

namespace {

constexpr std::uint16_t vendor_id = 0x0582;
constexpr std::uint16_t other_vendor = 0x1234;
constexpr std::uint16_t base_product = 0x0120;
constexpr std::uint16_t expansion_product = 0x0121;
constexpr std::uint16_t other_product = 0x0122;
constexpr std::uint32_t sample_rate = 48000;
constexpr std::uint8_t raw_flags = 0xa5;
constexpr unsigned int timeout_ms = 1500;
constexpr std::uint8_t vendor_in = 0xc0;

constexpr auto default_descriptors() {
  std::array<libusb_device_descriptor, 2> descriptors{};
  descriptors[0].idVendor = other_vendor;
  descriptors[0].idProduct = base_product;
  descriptors[1].idVendor = vendor_id;
  descriptors[1].idProduct = base_product;
  return descriptors;
}

struct FakeUsb {
  std::array<libusb_device_descriptor, 2> descriptors = default_descriptors();
  int init_error = 0;
  int list_error = 0;
  int descriptor_error = 0;
  int open_error = 0;
  int received = 4;
  unsigned int lists_freed = 0;
  unsigned int contexts_closed = 0;
  unsigned int handles_closed = 0;
  unsigned int opens = 0;
  unsigned int reads = 0;
};

FakeUsb io;
std::array<std::max_align_t, 2> device_tokens{};
std::max_align_t context_token{};
std::max_align_t handle_token{};
std::array<libusb_device *, 2> device_list{};

bool require(bool condition, std::string_view description) {
  if (!condition) { std::cerr << "FAIL: " << description << '\n'; }
  return condition;
}

bool both_personalities() {
  for (const auto product : {base_product, expansion_product}) {
    io = {};
    io.descriptors[1].idProduct = product;
    const auto status = octa::read_clock_status();
    if (!require(status && status->product_id == product && status->sample_rate_hz == sample_rate &&
                     status->flags == raw_flags && io.opens == 1 && io.reads == 1 &&
                     io.lists_freed == 1 && io.contexts_closed == 1 && io.handles_closed == 1,
                 "base/EXP use the bounded vendor read and retain their actual identity/flags")) {
      return false;
    }
  }
  io = {};
  io.descriptors[1].idProduct = other_product;
  const auto absent = octa::read_clock_status();
  return require(!absent && absent.error().code == LIBUSB_ERROR_NO_DEVICE && io.opens == 0 &&
                     io.reads == 0 && io.lists_freed == 1 && io.contexts_closed == 1,
                 "foreign manufacturer and neighboring product never open or receive requests");
}

bool failures() {
  for (int stage = 0; stage < 4; ++stage) {
    io = {};
    if (stage == 0) { io.init_error = LIBUSB_ERROR_ACCESS; }
    if (stage == 1) { io.list_error = LIBUSB_ERROR_ACCESS; }
    if (stage == 2) { io.descriptor_error = LIBUSB_ERROR_ACCESS; }
    if (stage == 3) { io.open_error = LIBUSB_ERROR_ACCESS; }
    const auto status = octa::read_clock_status();
    if (!require(
            !status && status.error().code == LIBUSB_ERROR_ACCESS && io.reads == 0 &&
                io.handles_closed == 0 && io.contexts_closed == (stage == 0 ? 0U : 1U) &&
                io.lists_freed == (stage < 2 ? 0U : 1U),
            "initialization/enumeration/descriptor/open errors release only acquired resources")) {
      return false;
    }
  }
  constexpr std::array<int, 6> replies = {0, 1, 2, 3, LIBUSB_ERROR_TIMEOUT, LIBUSB_ERROR_NO_DEVICE};
  for (const int received : replies) {
    io = {};
    io.received = received;
    const auto status = octa::read_clock_status();
    const int expected = received < 0 ? received : LIBUSB_ERROR_IO;
    if (!require(!status && status.error().code == expected && io.reads == 1 && io.opens == 1 &&
                     io.lists_freed == 1 && io.contexts_closed == 1 && io.handles_closed == 1,
                 "short/error reads preserve the error and close the opened target")) {
      return false;
    }
  }
  return true;
}

}

extern "C" int LIBUSB_CALL libusb_init(libusb_context **ctx) {
  if (io.init_error != 0) { return io.init_error; }
  *ctx = reinterpret_cast<libusb_context *>(&context_token);
  return LIBUSB_SUCCESS;
}

extern "C" void LIBUSB_CALL libusb_exit(libusb_context * /*context*/) {
  ++io.contexts_closed;
}

extern "C" ssize_t LIBUSB_CALL libusb_get_device_list(libusb_context * /*context*/,
                                                      libusb_device ***list) {
  if (io.list_error != 0) { return io.list_error; }
  for (std::size_t index = 0; index < device_list.size(); ++index) {
    device_list[index] = reinterpret_cast<libusb_device *>(&device_tokens[index]);
  }
  *list = device_list.data();
  return static_cast<ssize_t>(device_list.size());
}

extern "C" void LIBUSB_CALL libusb_free_device_list(libusb_device ** /*list*/, int unref_devices) {
  if (unref_devices == 1) { ++io.lists_freed; }
}

extern "C" int LIBUSB_CALL libusb_get_device_descriptor(libusb_device *dev,
                                                        libusb_device_descriptor *desc) {
  if (io.descriptor_error != 0) { return io.descriptor_error; }
  for (std::size_t index = 0; index < device_list.size(); ++index) {
    if (dev == device_list[index]) {
      *desc = io.descriptors[index];
      return LIBUSB_SUCCESS;
    }
  }
  return LIBUSB_ERROR_INVALID_PARAM;
}

extern "C" int LIBUSB_CALL libusb_open(libusb_device *dev, libusb_device_handle **dev_handle) {
  if (dev != device_list[1]) { return LIBUSB_ERROR_INVALID_PARAM; }
  if (io.open_error != 0) { return io.open_error; }
  ++io.opens;
  *dev_handle = reinterpret_cast<libusb_device_handle *>(&handle_token);
  return LIBUSB_SUCCESS;
}

extern "C" void LIBUSB_CALL libusb_close(libusb_device_handle * /*handle*/) {
  ++io.handles_closed;
}

extern "C" int LIBUSB_CALL libusb_control_transfer(libusb_device_handle *dev_handle,
                                                   std::uint8_t bmRequestType,
                                                   std::uint8_t bRequest,
                                                   std::uint16_t wValue,
                                                   std::uint16_t wIndex,
                                                   unsigned char *data,
                                                   std::uint16_t wLength,
                                                   unsigned int timeout) {
  constexpr auto expected_setup = std::tuple{
      vendor_in, std::uint8_t{3}, std::uint16_t{1}, std::uint16_t{0}, std::uint16_t{4}, timeout_ms};
  if (dev_handle != reinterpret_cast<libusb_device_handle *>(&handle_token) ||
      std::tuple{bmRequestType, bRequest, wValue, wIndex, wLength, timeout} != expected_setup) {
    return LIBUSB_ERROR_INVALID_PARAM;
  }
  ++io.reads;
  constexpr std::array<std::uint8_t, 4> reply = {0x80, 0xbb, 0, raw_flags};
  std::ranges::copy(reply, data);
  return io.received;
}

int main() {
  return both_personalities() && failures() ? 0 : 1;
}
