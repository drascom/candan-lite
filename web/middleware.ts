import { NextRequest, NextResponse } from 'next/server';

export function middleware(request: NextRequest) {
  if (process.env.NODE_ENV !== 'production') {
    return NextResponse.next();
  }

  const username = process.env.WEB_USERNAME;
  const password = process.env.WEB_PASSWORD;
  if (!username || !password) {
    return new NextResponse('WEB_USERNAME and WEB_PASSWORD must be configured', { status: 503 });
  }

  const authorization = request.headers.get('authorization');
  if (authorization?.startsWith('Basic ')) {
    try {
      const [givenUsername, givenPassword] = atob(authorization.slice(6)).split(':', 2);
      if (givenUsername === username && givenPassword === password) {
        return NextResponse.next();
      }
    } catch {
      // Fall through to the browser's Basic Auth challenge.
    }
  }

  return new NextResponse('Authentication required', {
    status: 401,
    headers: { 'WWW-Authenticate': 'Basic realm="Mate Voice", charset="UTF-8"' },
  });
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
