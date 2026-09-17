#ifndef XAUAI_V3107_M5_STRUCTURE_STOP_MQH
#define XAUAI_V3107_M5_STRUCTURE_STOP_MQH

#include "M5TargetStructure.mqh"

struct V3107StructureStopSelection
{
   bool   valid;
   double previous_extreme;
   double structure_extreme;
   double anchor;
   int    structure_bar_index;
   string source;
};

bool V3107LatestConfirmedM5PivotHigh(const V310Bar &bars[],double &level,int &bar_index)
{
   level=0.0;
   bar_index=-1;
   for(int i=2;i+2<ArraySize(bars);i++)
   {
      if(!V3103IsConfirmedM5PivotHigh(bars,i))
         continue;
      level=bars[i].high;
      bar_index=i;
      return true;
   }
   return false;
}

bool V3107LatestConfirmedM5PivotLow(const V310Bar &bars[],double &level,int &bar_index)
{
   level=0.0;
   bar_index=-1;
   for(int i=2;i+2<ArraySize(bars);i++)
   {
      if(!V3103IsConfirmedM5PivotLow(bars,i))
         continue;
      level=bars[i].low;
      bar_index=i;
      return true;
   }
   return false;
}

bool V3107SelectStructureStop(const V310Bar &bars[],const bool is_sell,
                              V3107StructureStopSelection &out)
{
   ZeroMemory(out);
   out.structure_bar_index=-1;
   if(ArraySize(bars)<2)
      return false;

   if(is_sell)
   {
      out.previous_extreme=bars[1].high;
      V3107LatestConfirmedM5PivotHigh(bars,out.structure_extreme,out.structure_bar_index);
      if(out.previous_extreme<=0.0)
         return false;
      out.anchor=MathMax(out.previous_extreme,out.structure_extreme);
   }
   else
   {
      out.previous_extreme=bars[1].low;
      V3107LatestConfirmedM5PivotLow(bars,out.structure_extreme,out.structure_bar_index);
      if(out.previous_extreme<=0.0)
         return false;
      out.anchor=(out.structure_extreme>0.0 ? MathMin(out.previous_extreme,out.structure_extreme)
                                           : out.previous_extreme);
   }

   if(out.structure_extreme<=0.0)
      out.source="PREVIOUS_BAR";
   else if(MathAbs(out.anchor-out.previous_extreme)<0.0000001 &&
           MathAbs(out.anchor-out.structure_extreme)<0.0000001)
      out.source="STRUCTURE_AND_PREVIOUS";
   else if(MathAbs(out.anchor-out.structure_extreme)<0.0000001)
      out.source="STRUCTURE_PIVOT";
   else
      out.source="PREVIOUS_BAR";

   out.valid=(out.anchor>0.0);
   return out.valid;
}

#endif
