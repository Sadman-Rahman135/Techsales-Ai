const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

// `now` is the server's render time (refreshed with the data), so rendering stays pure and hydration-stable.
export function When({ iso, timezone, now }: { iso: string; timezone: string; now: number }) {
  const date = new Date(iso);
  const minutes = Math.round((date.getTime() - now) / 60000);
  const text =
    Math.abs(minutes) < 60
      ? relative.format(minutes, "minute")
      : Math.abs(minutes) < 60 * 24
        ? relative.format(Math.round(minutes / 60), "hour")
        : relative.format(Math.round(minutes / 1440), "day");
  return (
    <time dateTime={iso} title={absoluteTime(iso, timezone)}>
      {text}
    </time>
  );
}

export function absoluteTime(iso: string, timezone: string): string {
  return new Date(iso).toLocaleString("en-US", { timeZone: timezone, dateStyle: "medium", timeStyle: "short" });
}
