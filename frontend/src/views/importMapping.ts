// Client-side Field_Mapping validation for the ImportDeck preview (Req 2.20, 2.22).
// Mirrors the server's `validate_mapping` in
// backend/app/services/anki_import/mapping.py; the server stays authoritative.
import type { FieldMapping } from '../api/types'

export type MappingRole = keyof FieldMapping

export type MappingErrorReason = 'missing' | 'out_of_range' | 'duplicate'

/** One entry per invalid role; empty when the mapping is valid. */
export type MappingErrors = Partial<Record<MappingRole, MappingErrorReason>>

export const MAPPING_ROLES: readonly MappingRole[] = ['word', 'reading', 'meaning']

/**
 * Validate a mapping against the selected note type's field count:
 * - `word` or `meaning` is null or outside `0..fieldCount-1` → `missing`
 * - `reading` is set but outside the range → `out_of_range`
 * - an in-range index shared with another role → `duplicate`, for every sharer
 */
export function validateMappingClient(
  mapping: FieldMapping,
  fieldCount: number,
): MappingErrors {
  const inRange = (index: number | null): index is number =>
    index !== null &&
    Number.isInteger(index) &&
    index >= 0 &&
    index < fieldCount

  const errors: MappingErrors = {}
  for (const role of MAPPING_ROLES) {
    const index = mapping[role]
    if (role === 'reading') {
      if (index !== null && !inRange(index)) errors[role] = 'out_of_range'
    } else if (!inRange(index)) {
      errors[role] = 'missing'
    }
  }

  for (const role of MAPPING_ROLES) {
    const index = mapping[role]
    if (errors[role] !== undefined || !inRange(index)) continue
    const shared = MAPPING_ROLES.some(
      (other) => other !== role && mapping[other] === index,
    )
    if (shared) errors[role] = 'duplicate'
  }

  return errors
}
