#ifndef XAUAI_V3109_C32B_TRADE_SLOT_POLICY_MQH
#define XAUAI_V3109_C32B_TRADE_SLOT_POLICY_MQH

#ifndef V3109C32_DIR_NONE
#define V3109C32_DIR_NONE -1
#define V3109C32_DIR_BUY 0
#define V3109C32_DIR_SELL 1
#endif

#define V3109C32_MAX_SLOTS 3
#define V3109C32_MIN_FILL_GAP_SECONDS 900
#define V3109C32_MAX_RISK_PER_SLOT_PERCENT 1.0
#define V3109C32_MAX_AGGREGATE_RISK_PERCENT 1.5

double V3109C32RiskBudgetUsd(const double equity,const double percent)
{
   if(equity<=0.0 || percent<=0.0) return 0.0;
   return equity*MathMin(percent,V3109C32_MAX_RISK_PER_SLOT_PERCENT)/100.0;
}

long V3109C32SlotMagic(const long base_magic,const int slot_index)
{
   if(base_magic<=0 || slot_index<0 || slot_index>=V3109C32_MAX_SLOTS)
      return -1;
   return base_magic+slot_index;
}

int V3109C32SlotIndexFromMagic(const long base_magic,const long observed_magic)
{
   if(base_magic<=0 || observed_magic<base_magic)
      return -1;
   const long offset=observed_magic-base_magic;
   if(offset<0 || offset>=V3109C32_MAX_SLOTS)
      return -1;
   return (int)offset;
}

bool V3109C32MagicBelongsToGroup(const long base_magic,const long observed_magic)
{
   return V3109C32SlotIndexFromMagic(base_magic,observed_magic)>=0;
}

double V3109C32SumGroupedValues(const long base_magic,const long &magics[],
                                const double &values[])
{
   if(ArraySize(magics)!=ArraySize(values)) return 0.0;
   double total=0.0;
   for(int i=0;i<ArraySize(magics);i++)
      if(V3109C32MagicBelongsToGroup(base_magic,magics[i])) total+=values[i];
   return total;
}

int V3109C32FindFirstFreeSlot(const bool &occupied[])
{
   if(ArraySize(occupied)!=V3109C32_MAX_SLOTS)
      return -1;
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      if(!occupied[slot])
         return slot;
   return -1;
}

bool V3109C32CanReserveSlot(const int occupied_slots,
                            const int exposure_direction,
                            const int candidate_direction,
                            const datetime last_fill_bar_time,
                            const datetime signal_bar_time,
                            const double account_equity,
                            const double configured_risk_percent,
                            const double aggregate_initial_risk_usd,
                            const double candidate_initial_risk_usd,
                            string &reason)
{
   reason="";
   if(occupied_slots<0 || occupied_slots>=V3109C32_MAX_SLOTS)
   {
      reason="SLOT_FULL";
      return false;
   }
   if(candidate_direction!=V3109C32_DIR_BUY && candidate_direction!=V3109C32_DIR_SELL)
   {
      reason="INVALID_DIRECTION";
      return false;
   }
   if(occupied_slots>0 && exposure_direction!=candidate_direction)
   {
      reason="DIRECTION_CONFLICT";
      return false;
   }
   if(signal_bar_time<=0)
   {
      reason="INVALID_SIGNAL_BAR";
      return false;
   }
   if(account_equity<=0.0)
   {
      reason="INVALID_ACCOUNT_EQUITY";
      return false;
   }
   if(configured_risk_percent<=0.0)
   {
      reason="INVALID_RISK_PERCENT";
      return false;
   }
   if(last_fill_bar_time>0 && signal_bar_time-last_fill_bar_time<V3109C32_MIN_FILL_GAP_SECONDS)
   {
      reason="FILL_GAP_LT_3_M5";
      return false;
   }
   const double slot_budget_usd=V3109C32RiskBudgetUsd(account_equity,
                                                      configured_risk_percent);
   if(candidate_initial_risk_usd<=0.0 ||
      candidate_initial_risk_usd>slot_budget_usd+0.01)
   {
      reason="SLOT_RISK_GT_CONFIGURED_PERCENT";
      return false;
   }
   if(aggregate_initial_risk_usd<0.0 ||
      aggregate_initial_risk_usd+candidate_initial_risk_usd>
         account_equity*V3109C32_MAX_AGGREGATE_RISK_PERCENT/100.0+0.01)
   {
      reason="AGGREGATE_RISK_GT_1_5_PERCENT";
      return false;
   }
   return true;
}

int V3109C32SelectAdmissibleSlot(const bool &occupied[],
                                 const int exposure_direction,
                                 const int candidate_direction,
                                 const datetime last_fill_bar_time,
                                 const datetime signal_bar_time,
                                 const double account_equity,
                                 const double configured_risk_percent,
                                 const double aggregate_initial_risk_usd,
                                 const double candidate_initial_risk_usd,
                                 string &reason)
{
   reason="";
   if(ArraySize(occupied)!=V3109C32_MAX_SLOTS)
   {
      reason="INVALID_SLOT_ARRAY";
      return -1;
   }
   int occupied_count=0;
   for(int slot=0;slot<V3109C32_MAX_SLOTS;slot++)
      if(occupied[slot]) occupied_count++;
   if(!V3109C32CanReserveSlot(occupied_count,exposure_direction,candidate_direction,
                              last_fill_bar_time,signal_bar_time,
                              account_equity,configured_risk_percent,
                              aggregate_initial_risk_usd,candidate_initial_risk_usd,
                              reason))
      return -1;
   const int slot=V3109C32FindFirstFreeSlot(occupied);
   if(slot<0) reason="SLOT_FULL";
   return slot;
}

int V3109C32ConsumeMatchingPending(const long base_magic,const long observed_magic,
                                   bool &active[])
{
   if(ArraySize(active)!=V3109C32_MAX_SLOTS) return -1;
   const int slot=V3109C32SlotIndexFromMagic(base_magic,observed_magic);
   if(slot<0 || !active[slot]) return -1;
   active[slot]=false;
   return slot;
}

#endif
