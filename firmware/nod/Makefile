.PHONY: test preview link-sim
CXX ?= g++
FLAGS = -std=c++11 -Wall -Wextra -Werror -O1
BUILD = build
test: ; mkdir -p $(BUILD) && $(CXX) $(FLAGS) tests/test_nod.cpp -o $(BUILD)/nod_test && $(BUILD)/nod_test
preview: ; mkdir -p $(BUILD) && $(CXX) $(FLAGS) tools/gen_preview.cpp -o $(BUILD)/nod_gen && $(BUILD)/nod_gen > preview/layouts.js
link-sim: ; mkdir -p $(BUILD) && $(CXX) $(FLAGS) tools/link_sim.cpp -o $(BUILD)/link_sim && $(BUILD)/link_sim
