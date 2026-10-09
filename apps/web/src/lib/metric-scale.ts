import type { MetricDefinition, PublicMetric } from "@/lib/public-api";

export type MetricScalePosition = {
  readonly value: number;
  readonly rawRatio: number;
  readonly performanceRatio: number;
  readonly low: number;
  readonly high: number;
};

export function metricDomain(definition: MetricDefinition): readonly [number, number] | null {
  const low = Number(definition.domain[0]);
  const high = Number(definition.domain[1]);
  return Number.isFinite(low) && Number.isFinite(high) && high > low ? [low, high] : null;
}

export function metricScalePosition(
  metric: PublicMetric | undefined,
  definition: MetricDefinition | undefined,
): MetricScalePosition | null {
  if (!metric || !definition || metric.status !== "measured" || metric.value === null) return null;
  const domain = metricDomain(definition);
  const value = Number(metric.value);
  if (!domain || !Number.isFinite(value) || value < domain[0] || value > domain[1]) return null;

  const [low, high] = domain;
  const rawRatio = (value - low) / (high - low);
  return {
    value,
    rawRatio,
    performanceRatio: definition.direction === "lower" ? 1 - rawRatio : rawRatio,
    low,
    high,
  };
}
