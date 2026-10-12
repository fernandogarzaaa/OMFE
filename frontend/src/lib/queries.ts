import { useQuery } from '@tanstack/react-query';
import { api, itemsOf } from './api';

export function useCreators() {
  const q = useQuery({ queryKey: ['creators'], queryFn: () => api.creators.list({ limit: 200 }) });
  const creators = itemsOf(q.data);
  const nameOf = (id: string | null | undefined) => (id ? creators.find((c) => c.id === id)?.name ?? id : 'Unassigned');
  return { ...q, creators, nameOf };
}
