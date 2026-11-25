#pragma once
namespace maz { namespace dictate {
enum class Target { Field, Pc, Craft };
inline Target targetForPc(bool craft) { return craft ? Target::Craft : Target::Pc; }
}}
