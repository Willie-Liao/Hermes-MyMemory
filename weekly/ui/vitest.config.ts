import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: [
      'src/fourPartBrief.test.ts',
      'src/viewScroll.test.ts',
      'src/weeklyReorganise.test.ts',
      'src/weeklyJson.test.ts',
    ],
  },
});
