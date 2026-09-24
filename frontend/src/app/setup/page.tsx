import { ModelsPanel } from "@/components/setup/ModelsPanel";
import { ModePanel } from "@/components/setup/ModePanel";
import { ReadinessPanel } from "@/components/setup/ReadinessPanel";
import { RobinhoodPanel } from "@/components/setup/RobinhoodPanel";

export default function SetupPage() {
  return (
    <div className="grid grid-cols-12 gap-2">
      <ModePanel className="col-span-12 xl:col-span-7" />
      <RobinhoodPanel className="col-span-12 xl:col-span-5" />
      <ReadinessPanel className="col-span-12 xl:col-span-7" />
      <ModelsPanel className="col-span-12 xl:col-span-5" />
    </div>
  );
}
