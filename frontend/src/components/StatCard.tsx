type StatCardProps = {
  title: string;
  value: string;
  description?: string;
  footnote?: string;
};

export function StatCard({ title, value, description, footnote }: StatCardProps) {
  return (
    <article className="page-surface stats-card">
      <p className="stats-card-title">{title}</p>
      <p className="stats-card-value">{value}</p>
      {description ? <p className="stats-card-description">{description}</p> : null}
      {footnote ? <p className="stats-card-footnote">{footnote}</p> : null}
    </article>
  );
}
