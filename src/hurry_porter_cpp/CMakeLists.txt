cmake_minimum_required(VERSION 3.16)
project(hurry_porter_cpp)

set(Python3_EXECUTABLE "/usr/bin/python3.12" CACHE FILEPATH "ROS Jazzy Python interpreter" FORCE)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)

add_executable(hurry_latency_probe src/latency_probe.cpp)
ament_target_dependencies(hurry_latency_probe rclcpp)
target_compile_features(hurry_latency_probe PRIVATE cxx_std_17)

install(TARGETS hurry_latency_probe
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
