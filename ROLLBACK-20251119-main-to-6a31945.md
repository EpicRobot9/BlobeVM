Rollback performed: 2025-11-19
Target commit: 6a31945dce6082cd5fd2a3878eb3bc5efcd81139

Actions taken:

- Reset local `main` to the target commit:

  ```bash
  git reset --hard 6a31945dce6082cd5fd2a3878eb3bc5efcd81139
  ```

- Force-pushed local `main` to `origin`:

  ```bash
  git push --force origin main
  ```

- Created and pushed annotated tag:

  ```bash
  git tag -a rollback-to-6a31945-20251119 6a31945dce6082cd5fd2a3878eb3bc5efcd81139 -m "Rollback main to 6a31945 on 2025-11-19"
  git push origin rollback-to-6a31945-20251119
  ```

Notes:

- This is a forced history rewrite on `origin/main`. Inform collaborators to rebase or reset their local `main` to the tag or the commit.
- If you want a different tag name or additional metadata, update this file accordingly.
