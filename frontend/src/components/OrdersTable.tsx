import Link from "next/link";
import { Empty } from "@/components/ui/States";
import { dateTime, num, upper } from "@/lib/format";
import type { Order } from "@/lib/types";

const STATUS_TONE: Record<string, string> = {
  filled: "text-phos",
  partial: "text-amber",
  open: "text-cyan",
  pending: "text-cyan",
  cancelled: "text-dim",
  rejected: "text-danger",
};

export function OrdersTable({ orders, showLinks = false }: { orders: Order[]; showLinks?: boolean }) {
  if (orders.length === 0) return <Empty>no orders</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th>time</th>
            <th>side</th>
            <th className="r">qty</th>
            <th>instrument</th>
            <th className="r">limit</th>
            <th className="r">filled</th>
            <th className="r">avg fill</th>
            <th>reason</th>
            <th>status</th>
            <th>message</th>
            {showLinks ? <th>trace</th> : null}
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id}>
              <td className="text-dim">{dateTime(o.ts)}</td>
              <td className={o.side === "buy" ? "text-phos" : "text-danger"}>{upper(o.side)}</td>
              <td className="r">{num(o.qty, 0)}</td>
              <td className="font-bold">{o.instrument_key}</td>
              <td className="r">{num(o.limit_price)}</td>
              <td className="r">
                {num(o.filled_qty, 0)}
                <span className="text-dim">/{num(o.qty, 0)}</span>
              </td>
              <td className="r">{num(o.avg_fill_price, 4)}</td>
              <td>{upper(o.reason)}</td>
              <td className={STATUS_TONE[o.status] ?? "text-fg"}>{upper(o.status)}</td>
              <td className="max-w-[260px] truncate text-dim" title={o.message}>
                {o.message}
              </td>
              {showLinks ? (
                <td className="space-x-2 text-xs">
                  {o.decision_id ? (
                    <Link className="link" href={`/decisions/${o.decision_id}`}>
                      dec
                    </Link>
                  ) : null}
                  {o.position_id ? (
                    <Link className="link" href={`/positions/${o.position_id}`}>
                      pos
                    </Link>
                  ) : null}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
