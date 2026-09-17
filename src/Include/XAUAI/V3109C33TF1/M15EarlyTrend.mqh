#ifndef XAUAI_V3109_C33_TF1_M15_EARLY_TREND_MQH
#define XAUAI_V3109_C33_TF1_M15_EARLY_TREND_MQH

// C3.3-TF1 gives a newly emerging M15 trend a narrow probationary route before
// the slower 20-wing primary structure has completed an HH/HL or LL/LH cycle.
// This policy does not declare a new primary trend and never grants the
// opposite direction.  Its only effect is to authorize one M5 trend slot.
enum ENUM_V3109_C33_EARLY_TREND
{
   V3109C33_EARLY_NONE=0,
   V3109C33_EARLY_BUY=1,
   V3109C33_EARLY_SELL=2
};

struct V3109C33EarlyTrendInput
{
   int    primary_state;
   int    macro_bias;
   double close_price;
   double ema20;
   double ema_slope_atr;
   double atr14;
   double reference_high;
   double reference_low;
   int    above_ema_count4;
   int    below_ema_count4;
   bool   protected_low_intact;
   bool   protected_high_intact;
   double tick_size;
};

ENUM_V3109_C33_EARLY_TREND V3109C33EvaluateEarlyTrend(
   const V3109C33EarlyTrendInput &in)
{
   // The host primary enum uses zero for PRIMARY_RANGE.  Transition states are
   // deliberately excluded because they contain unresolved opposite evidence.
   if(in.primary_state!=0 || in.atr14<=0.0 || in.tick_size<=0.0 ||
      in.close_price<=0.0 || in.ema20<=0.0)
      return V3109C33_EARLY_NONE;

   const double breakout_buffer=MathMax(2.0*in.tick_size,0.05*in.atr14);
   const bool early_buy=in.macro_bias>=0 && in.protected_low_intact &&
      in.reference_high>0.0 && in.close_price>in.ema20 &&
      in.ema_slope_atr>=0.01 && in.above_ema_count4>=3 &&
      in.close_price>=in.reference_high+breakout_buffer;
   const bool early_sell=in.macro_bias<=0 && in.protected_high_intact &&
      in.reference_low>0.0 && in.close_price<in.ema20 &&
      in.ema_slope_atr<=-0.01 && in.below_ema_count4>=3 &&
      in.close_price<=in.reference_low-breakout_buffer;

   if(early_buy && !early_sell) return V3109C33_EARLY_BUY;
   if(early_sell && !early_buy) return V3109C33_EARLY_SELL;
   return V3109C33_EARLY_NONE;
}

bool V3109C33EarlySlotAllowed(const bool early_trend,const int occupied_slots)
{
   if(occupied_slots<0) return false;
   return !early_trend || occupied_slots==0;
}

#endif
