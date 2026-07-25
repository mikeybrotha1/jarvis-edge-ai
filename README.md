# Jarvis Edge AI

A locally hosted edge-AI platform built on Raspberry Pi 5 and the Raspberry Pi AI HAT+ 2 with the Hailo-10H accelerator.

## Project 1: Real-Time Object Detection

This first project deploys hardware-accelerated computer vision on the Raspberry Pi 5. The system processes video locally and identifies people, vehicles, bags, and other common objects with confidence scores.

## Current Status

- Raspberry Pi 5 operational
- Hailo-10H detected over PCIe
- `hailo1x_pci` kernel driver loaded
- `/dev/hailo0` available
- Hailo firmware responding
- Real-time object detection demo running

## Hardware

- Raspberry Pi 5
- Raspberry Pi AI HAT+ 2
- Hailo-10H AI accelerator
- Active cooling
- Raspberry Pi OS

## Technologies

- Python
- Linux
- HailoRT
- Hailo Apps
- GStreamer
- Computer vision
- Edge AI
- Git

## Planned Enhancements

1. Run inference against a custom video
2. Add a live camera source
3. Capture FPS, CPU, memory, and temperature metrics
4. Save detection events
5. Add a dashboard
6. Integrate the vision service into Jarvis
