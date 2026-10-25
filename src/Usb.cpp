// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Usb.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <libusb.h>
#include <memory>
#include <span>

namespace octa {
namespace {

constexpr std::uint16_t vendor_id = 0x0582;
constexpr std::uint16_t product_id = 0x0120;
constexpr std::uint16_t expansion_product_id = 0x0121;
constexpr std::uint8_t vendor_in = 0xc0;
constexpr std::uint8_t clock_request = 3;
constexpr std::uint16_t clock_status_value = 1;
constexpr unsigned int timeout_ms = 1500;
using UsbHandle = std::unique_ptr<libusb_device_handle, decltype(&libusb_close)>;

struct OpenedDevice {
  UsbHandle handle;
  std::uint16_t product_id;
};

struct FreeDeviceList {
  void operator()(libusb_device **devices) const noexcept { libusb_free_device_list(devices, 1); }
};

std::expected<OpenedDevice, UsbError> find_device(libusb_context *context) {
  libusb_device **raw_list = nullptr;
  const auto count = libusb_get_device_list(context, &raw_list);
  if (count < 0) { return std::unexpected(UsbError{.code = static_cast<int>(count)}); }
  const std::unique_ptr<libusb_device *, FreeDeviceList> list(raw_list);
  const std::span<libusb_device *const> devices(list.get(), static_cast<std::size_t>(count));
  for (auto *device : devices) {
    libusb_device_descriptor descriptor{};
    const int result = libusb_get_device_descriptor(device, &descriptor);
    if (result != LIBUSB_SUCCESS) { return std::unexpected(UsbError{.code = result}); }
    if (descriptor.idVendor != vendor_id ||
        (descriptor.idProduct != product_id && descriptor.idProduct != expansion_product_id)) {
      continue;
    }
    libusb_device_handle *raw_handle = nullptr;
    const int opened = libusb_open(device, &raw_handle);
    if (opened != LIBUSB_SUCCESS) { return std::unexpected(UsbError{.code = opened}); }
    return OpenedDevice{.handle = UsbHandle(raw_handle, &libusb_close),
                        .product_id = descriptor.idProduct};
  }
  return std::unexpected(UsbError{.code = LIBUSB_ERROR_NO_DEVICE});
}

}

std::expected<ClockStatus, UsbError> read_clock_status() {
  libusb_context *raw_context = nullptr;
  const int initialized = libusb_init(&raw_context);
  if (initialized != LIBUSB_SUCCESS) { return std::unexpected(UsbError{.code = initialized}); }
  const std::unique_ptr<libusb_context, decltype(&libusb_exit)> context(raw_context, &libusb_exit);
  const auto device = find_device(context.get());
  if (!device) { return std::unexpected(device.error()); }
  std::array<std::uint8_t, 4> data{};
  const int received = libusb_control_transfer(device->handle.get(),
                                               vendor_in,
                                               clock_request,
                                               clock_status_value,
                                               0,
                                               data.data(),
                                               static_cast<std::uint16_t>(data.size()),
                                               timeout_ms);
  if (received < 0) { return std::unexpected(UsbError{.code = received}); }
  if (received != static_cast<int>(data.size())) {
    return std::unexpected(UsbError{.code = LIBUSB_ERROR_IO});
  }
  constexpr unsigned int middle_shift = 8;
  constexpr unsigned int high_shift = 16;
  const auto rate = static_cast<std::uint32_t>(data[0]) |
                    (static_cast<std::uint32_t>(data[1]) << middle_shift) |
                    (static_cast<std::uint32_t>(data[2]) << high_shift);
  return ClockStatus{.sample_rate_hz = rate, .flags = data[3], .product_id = device->product_id};
}

}
