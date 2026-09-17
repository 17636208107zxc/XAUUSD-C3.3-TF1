#ifndef XAUAI_V3103_M5_ROUTE_SELECTION_MQH
#define XAUAI_V3103_M5_ROUTE_SELECTION_MQH

#include "Strategy01Types.mqh"

enum ENUM_V3103_M5_ROUTE
{
   V3103_ROUTE_NONE=0,
   V3103_ROUTE_H2_ORIGINAL=1,
   V3103_ROUTE_H2_RECOVERY_BREAK=2,
   V3103_ROUTE_EMA_RECOVERY=3,
   V3103_ROUTE_EMA_COMPRESSION_BREAK=4
};

ENUM_V3103_M5_ROUTE V3103SelectM5Route(const bool original_h2,const bool recovery_break,
                                       const bool ema_recovery,const bool compression_break)
{
   if(original_h2) return V3103_ROUTE_H2_ORIGINAL;
   if(recovery_break) return V3103_ROUTE_H2_RECOVERY_BREAK;
   if(ema_recovery) return V3103_ROUTE_EMA_RECOVERY;
   if(compression_break) return V3103_ROUTE_EMA_COMPRESSION_BREAK;
   return V3103_ROUTE_NONE;
}

// Direction uses the host EA's canonical numeric values: BUY=0, SELL=1.
// Keeping it as int lets the pure helper remain independent of the legacy EA type.
string V3103SetupKey(const int direction,const datetime pullback_start_time)
{
   if(pullback_start_time<=0 || (direction!=0 && direction!=1)) return "";
   return (direction==0 ? "BUY|" : "SELL|")+IntegerToString((int)pullback_start_time);
}

bool V3103CanEmitSetupCandidate(const string setup_key,const string emitted_setup_key)
{
   return StringLen(setup_key)>0 && setup_key!=emitted_setup_key;
}

// A V3.10.3 lifecycle identity is the direction plus the already-closed M5
// signal bar. It deliberately does not include the pullback start: a later,
// independently valid recovery bar may create a fresh plan after the prior
// candidate/order has ended.
string V3103SignalKey(const int direction,const datetime signal_bar_time)
{
   if(signal_bar_time<=0 || (direction!=0 && direction!=1)) return "";
   return (direction==0 ? "BUY|" : "SELL|")+IntegerToString((int)signal_bar_time);
}

bool V3103CanEmitSignalCandidate(const string signal_key,const string emitted_signal_key)
{
   return StringLen(signal_key)>0 && signal_key!=emitted_signal_key;
}

// The caller supplies the live own-order/position state. Keeping this helper
// pure makes the re-arm contract regression-testable without trade runtime.
bool V3103MayFormNewCandidate(const bool own_pending_or_position,
                              const string signal_key,const string emitted_signal_key)
{
   return !own_pending_or_position && V3103CanEmitSignalCandidate(signal_key,emitted_signal_key);
}

// While Route A is actively evaluating an unlocked pullback it keeps priority.
// A completed/locked Route A setup must not suppress independent Route B recovery.
bool V3103IndependentEMARouteMayRun(const bool legacy_pullback_active,const bool legacy_pullback_locked)
{
   return !legacy_pullback_active || legacy_pullback_locked;
}

#endif
