import { Pipe, PipeTransform } from '@angular/core';

@Pipe({ name: 'uniqueValues', standalone: true })
export class UniqueValuesPipe implements PipeTransform {
  transform(value: any[]): any[] {
    if (!Array.isArray(value)) return [];
    return [...new Set(value)];
  }
}
