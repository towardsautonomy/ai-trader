import { DecisionFeed } from "@/components/dashboard/DecisionFeed";
import { EquityPanel } from "@/components/dashboard/EquityPanel";
import { EventStream } from "@/components/dashboard/EventStream";
import { MarketPanel } from "@/components/dashboard/MarketPanel";
import { PositionsTable } from "@/components/dashboard/PositionsTable";
import { StatsPanel } from "@/components/dashboard/StatsPanel";
import { StatusBar } from "@/components/dashboard/StatusBar";

export default function TerminalPage() {
  return (
    <div className="grid grid-cols-12 gap-2">
      <div className="col-span-12">
        <StatusBar />
      </div>
      <PositionsTable className="col-span-12 h-[270px] xl:col-span-8" />
      <MarketPanel className="col-span-12 xl:col-span-4 xl:h-[270px]" />
      <EventStream className="col-span-12 h-[380px] xl:col-span-6" />
      <DecisionFeed className="col-span-12 h-[380px] xl:col-span-6" />
      <EquityPanel className="col-span-12 h-[280px] xl:col-span-7" />
      <StatsPanel className="col-span-12 xl:col-span-5 xl:h-[280px]" />
    </div>
  );
}
