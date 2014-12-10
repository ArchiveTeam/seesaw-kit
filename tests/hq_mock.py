'''Mocks Warrior HQ.'''
import tornado.web


class RegisterHandler(tornado.web.RequestHandler):
    def initialize(self, **kwargs):
        pass

    def post(self):
        self.write({
            'warrior_id': 'test-warrior-id',
        })


class UpdateHandler(tornado.web.RedirectHandler):
    def initialize(self, **kwargs):
        pass

    def post(self):
        self.write({
            "warrior": {
                "seesaw_version": "0.0.15"
            },
            "broadcast_message": "<i>Hello world</i>",
            "auto_project": "testproject",
            "projects": [
                {
                    "name": "testproject",
                    "title": "A test project",
                    "description": "Testing a project",
                    "repository":
                        "https://github.com/ArchiveTeam/example-seesaw-project",
                    "logo":
                        "https://raw.github.com/ArchiveTeam/warrior-preseed/master"
                        "/splash/Archive_team-white.png",
                    "marker_html": "hi",
                    "lat_lng": [
                        0.0,
                        0.0
                    ],
                    "leaderboard": "http://example.com/"
                },
                {
                    "name": "localproject",
                    "title": "A local project",
                    "description":
                        "A project loaded from /tmp/mywarriorproject "
                        "Useful for testing auto update project",
                    "repository":
                        "/tmp/mywarriorproject",
                    "logo":
                        "",
                    "marker_html": "hi",
                    "lat_lng": [
                        0.0,
                        0.0
                    ],
                    "leaderboard": "http://example.com/"
                },
            ]
        })


if __name__ == '__main__':
    handlers = [
        (r'/api/register.json', RegisterHandler),
        (r'/api/update.json', UpdateHandler),
    ]
    app = tornado.web.Application(handlers=handlers)

    app.listen(8681, 'localhost')
    tornado.ioloop.IOLoop.instance().start()
