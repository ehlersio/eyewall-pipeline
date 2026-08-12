-- Hard-tier trivia batch 2 -- correction + 56 new rows (43 NHL, 13 PWHL).
-- Run this in the Supabase SQL editor -- this repo has no migration
-- tooling, same convention as docs/session92_trivia_tables.sql and
-- docs/session_hard_trivia_batch.sql.
--
-- ============================================================
-- PART 1: CORRECTION -- run this first, it's urgent.
-- ============================================================
-- docs/session_hard_trivia_batch.sql (already applied) shipped a real
-- factual error on the 2026-08-14 PWHL row: it named the "Isobel Cup" as
-- the PWHL's championship trophy. That's wrong -- the Isobel Cup belongs
-- to the PHF (formerly NWHL), a separate, now-defunct league. The PWHL's
-- actual championship trophy, first awarded in 2024, is the Walter Cup,
-- named for owner Mark Walter. Verified via Wikipedia's "Walter Cup" and
-- "Isobel Cup" pages plus ESPN/CBC coverage of the trophy's 2024 unveiling.
-- This UPDATE fixes that row in place rather than leaving a wrong answer
-- live. The other two existing rows that mention "Isobel Cup" (2026-08-15,
-- CWHL/Clarkson Cup; 2026-08-22, Patty Kazmaier Award) use it correctly as
-- a plausible wrong-answer distractor and don't need touching.

update public.trivia_questions
set
  question_text = 'What is the name of the trophy awarded to the PWHL''s playoff champion?',
  options = '["Isobel Cup", "Walter Cup", "Clarkson Cup", "Kazmaier Cup"]'::jsonb,
  correct_index = 1,
  explanation = 'The Walter Cup, named for PWHL owner Mark Walter, has been the league''s championship trophy since 2024 -- the Isobel Cup instead belongs to the PHF (formerly NWHL), a separate, now-defunct league.'
where question_date = '2026-08-14' and tier = 'hard' and sport = 'pwhl' and team = 'ALL';

-- ============================================================
-- PART 2: 56 new rows (43 NHL, 13 PWHL), continuing the existing
-- date sequences -- NHL from 2026-09-07 (previous batch ended
-- 2026-09-06), PWHL from 2026-08-25 (previous batch ended 2026-08-24).
-- ============================================================
--
-- Sourced this round from verified year-by-year lists (Wikipedia's
-- Stanley Cup champions, Conn Smythe Trophy, and first-overall-draft-pick
-- pages, cross-checked against live search results for anything from the
-- last two seasons) plus a detailed PWHL history pull -- not the general
-- trivia-site scrape used in the first batch. Every fact below is tied to
-- a specific year/pick/trophy from those sourced lists, not general
-- knowledge recall alone. correct_index is shuffled per row (not always
-- 0) to avoid a learnable answer-position pattern.
--
-- Two facts here are from the current 2025-26 season, beyond most
-- pretrained knowledge, confirmed via live search: the 2026 Stanley Cup
-- (Carolina over Vegas, Carolina's second title) and Jordan Staal becoming
-- the oldest-ever Conn Smythe winner at 37.

insert into public.trivia_questions
  (question_date, tier, sport, question_text, options, correct_index, explanation, source)
values

-- ============================== NHL (43) ==============================
('2026-09-07', 'hard', 'nhl',
 'Which team won the Stanley Cup in 1990, two years after trading away Wayne Gretzky?',
 '["Edmonton Oilers", "Los Angeles Kings", "Calgary Flames", "Boston Bruins"]'::jsonb, 0,
 'The Oilers won the Cup in 1990, proving they could still win it without Gretzky, who''d been traded to LA in 1988.', 'curated'),

('2026-09-08', 'hard', 'nhl',
 'Which team''s 1994 Stanley Cup win ended a 54-year championship drought?',
 '["Boston Bruins", "Vancouver Canucks", "Toronto Maple Leafs", "New York Rangers"]'::jsonb, 3,
 'The Rangers beat the Canucks in 1994 for their first Cup since 1940.', 'curated'),

('2026-09-09', 'hard', 'nhl',
 'Which team swept the heavily favored Detroit Red Wings to win the 1995 Stanley Cup?',
 '["New York Islanders", "Pittsburgh Penguins", "New Jersey Devils", "Philadelphia Flyers"]'::jsonb, 2,
 'The Devils swept Detroit in four games in the 1995 Final, a major upset.', 'curated'),

('2026-09-10', 'hard', 'nhl',
 'Which team won the controversial 1999 Stanley Cup Final, clinched by Brett Hull''s overtime goal with his skate arguably in the crease?',
 '["St. Louis Blues", "Dallas Stars", "Buffalo Sabres", "Colorado Avalanche"]'::jsonb, 1,
 'Brett Hull''s Game 6 overtime goal won the 1999 Cup for Dallas over Buffalo, and the crease call is still debated today.', 'curated'),

('2026-09-11', 'hard', 'nhl',
 'Which team won its first Stanley Cup in 2004, defeating the Calgary Flames?',
 '["Calgary Flames", "Carolina Hurricanes", "San Jose Sharks", "Tampa Bay Lightning"]'::jsonb, 3,
 'The Tampa Bay Lightning won their first championship in 2004, beating Calgary in seven games.', 'curated'),

('2026-09-12', 'hard', 'nhl',
 'Which team won the 2003 Stanley Cup, defeating the Mighty Ducks of Anaheim?',
 '["New Jersey Devils", "Dallas Stars", "Minnesota Wild", "Ottawa Senators"]'::jsonb, 0,
 'The Devils beat the Mighty Ducks of Anaheim in seven games for the 2003 Cup.', 'curated'),

('2026-09-13', 'hard', 'nhl',
 'In what year did the Carolina Hurricanes win their first Stanley Cup championship?',
 '["2002", "2006", "2009", "1999"]'::jsonb, 1,
 'Carolina beat the Edmonton Oilers in 2006 for the franchise''s first Stanley Cup.', 'curated'),

('2026-09-14', 'hard', 'nhl',
 'Which team defeated the Ottawa Senators to win the 2007 Stanley Cup?',
 '["San Jose Sharks", "Anaheim Ducks", "Nashville Predators", "Buffalo Sabres"]'::jsonb, 1,
 'The Anaheim Ducks beat the Ottawa Senators in five games to win the 2007 Cup.', 'curated'),

('2026-09-15', 'hard', 'nhl',
 'Which team won the 2010 Stanley Cup, ending a 49-year championship drought?',
 '["St. Louis Blues", "Philadelphia Flyers", "Chicago Blackhawks", "Vancouver Canucks"]'::jsonb, 2,
 'The Blackhawks beat the Flyers in 2010 for their first Cup since 1961.', 'curated'),

('2026-09-16', 'hard', 'nhl',
 'Which team, seeded 8th in the Western Conference, won the 2012 Stanley Cup?',
 '["New Jersey Devils", "Phoenix Coyotes", "St. Louis Blues", "Los Angeles Kings"]'::jsonb, 3,
 'The eighth-seeded LA Kings won the 2012 Cup, defeating the New Jersey Devils.', 'curated'),

('2026-09-17', 'hard', 'nhl',
 'Which team won its first Stanley Cup in 2018, giving Alex Ovechkin his only championship to date?',
 '["Tampa Bay Lightning", "Washington Capitals", "Pittsburgh Penguins", "Vegas Golden Knights"]'::jsonb, 1,
 'Washington beat the expansion Vegas Golden Knights in 2018 for the franchise''s first Cup.', 'curated'),

('2026-09-18', 'hard', 'nhl',
 'Which team went from last place in the NHL standings in January to Stanley Cup champions that same season, 2018-19?',
 '["Boston Bruins", "Carolina Hurricanes", "Columbus Blue Jackets", "St. Louis Blues"]'::jsonb, 3,
 'The Blues were last in the NHL standings on January 3, 2019, and went on to win the Stanley Cup that June.', 'curated'),

('2026-09-19', 'hard', 'nhl',
 'Which expansion franchise won its first Stanley Cup in just its sixth season of existence, in 2023?',
 '["Seattle Kraken", "Nashville Predators", "Florida Panthers", "Vegas Golden Knights"]'::jsonb, 3,
 'The Vegas Golden Knights, an expansion team since 2017, won their first Cup in 2023, beating the Florida Panthers.', 'curated'),

('2026-09-20', 'hard', 'nhl',
 'Which team won its first Stanley Cup championship in franchise history in 2024, defeating the Edmonton Oilers?',
 '["Dallas Stars", "Vegas Golden Knights", "Florida Panthers", "New York Rangers"]'::jsonb, 2,
 'The Florida Panthers won their first Cup in 2024, beating Edmonton in seven games.', 'curated'),

('2026-09-21', 'hard', 'nhl',
 'Which team won back-to-back Stanley Cups in 2024 and 2025, both times defeating the Edmonton Oilers?',
 '["Florida Panthers", "Colorado Avalanche", "Vegas Golden Knights", "Tampa Bay Lightning"]'::jsonb, 0,
 'The Florida Panthers beat Edmonton in the Final in both 2024 and 2025, the first repeat champion since Tampa Bay in 2020-21.', 'curated'),

('2026-09-22', 'hard', 'nhl',
 'Which team won the 2026 Stanley Cup, its second championship after first winning in 2006?',
 '["New Jersey Devils", "Carolina Hurricanes", "Washington Capitals", "Vegas Golden Knights"]'::jsonb, 1,
 'The Carolina Hurricanes beat the Vegas Golden Knights in six games to win the 2026 Stanley Cup, their second title.', 'curated'),

('2026-09-23', 'hard', 'nhl',
 'Which defenseman won the Conn Smythe Trophy in 1970 after scoring the Cup-clinching overtime goal captured in hockey''s most famous photo?',
 '["Brad Park", "Denis Potvin", "Larry Robinson", "Bobby Orr"]'::jsonb, 3,
 'Bobby Orr''s diving Cup-winning goal against St. Louis in 1970 became one of hockey''s most iconic images.', 'curated'),

('2026-09-24', 'hard', 'nhl',
 'Which 20-year-old rookie goaltender won the Conn Smythe Trophy in 1986, leading Montreal to an unexpected Cup?',
 '["Ken Dryden", "Mike Vernon", "Grant Fuhr", "Patrick Roy"]'::jsonb, 3,
 'Patrick Roy won the Conn Smythe in his first full NHL season, backstopping the Canadiens to the 1986 Cup.', 'curated'),

('2026-09-25', 'hard', 'nhl',
 'Who is the only player to win the Conn Smythe Trophy three times?',
 '["Sidney Crosby", "Wayne Gretzky", "Mario Lemieux", "Patrick Roy"]'::jsonb, 3,
 'Patrick Roy won the Conn Smythe in 1986, 1993, and 2001 -- no other player has won it more than twice.', 'curated'),

('2026-09-26', 'hard', 'nhl',
 'Which player won his only career Conn Smythe Trophy in 2018, the same year he won his first Stanley Cup?',
 '["Braden Holtby", "Nicklas Backstrom", "Alexander Ovechkin", "T.J. Oshie"]'::jsonb, 2,
 'Ovechkin won the Conn Smythe as Washington won its first Stanley Cup in 2018.', 'curated'),

('2026-09-27', 'hard', 'nhl',
 'Which defenseman won the 2022 Conn Smythe Trophy as playoff MVP?',
 '["Mikko Rantanen", "Nathan MacKinnon", "Darcy Kuemper", "Cale Makar"]'::jsonb, 3,
 'Cale Makar won the Conn Smythe in 2022, leading Colorado''s Cup run from the blue line.', 'curated'),

('2026-09-28', 'hard', 'nhl',
 'Who became the first Florida Panthers player ever to win the Conn Smythe Trophy, in 2025?',
 '["Sam Bennett", "Sergei Bobrovsky", "Matthew Tkachuk", "Aleksander Barkov"]'::jsonb, 0,
 'Sam Bennett won the 2025 Conn Smythe, the first Panthers player to win it.', 'curated'),

('2026-09-29', 'hard', 'nhl',
 'Which Carolina Hurricanes player became the oldest Conn Smythe Trophy winner in NHL history in 2026, at age 37?',
 '["Andrei Svechnikov", "Frederik Andersen", "Jordan Staal", "Sebastian Aho"]'::jsonb, 2,
 'Jordan Staal won the 2026 Conn Smythe at 37 years, 277 days old, the oldest winner in the award''s history.', 'curated'),

('2026-09-30', 'hard', 'nhl',
 'Which player won back-to-back Conn Smythe Trophies in 2016 and 2017?',
 '["Alexander Ovechkin", "Sidney Crosby", "Patrick Kane", "Jonathan Toews"]'::jsonb, 1,
 'Sidney Crosby won the Conn Smythe in both 2016 and 2017 as Pittsburgh won consecutive Cups.', 'curated'),

('2026-10-01', 'hard', 'nhl',
 'Which player won back-to-back Conn Smythe Trophies in 1991 and 1992, leading Pittsburgh to its first two Stanley Cups?',
 '["Mario Lemieux", "Tom Barrasso", "Ron Francis", "Jaromir Jagr"]'::jsonb, 0,
 'Mario Lemieux won the Conn Smythe in both 1991 and 1992 as the Penguins won their first two championships.', 'curated'),

('2026-10-02', 'hard', 'nhl',
 'Which longtime Detroit Red Wings captain won the Conn Smythe Trophy in 1998?',
 '["Nicklas Lidstrom", "Steve Yzerman", "Sergei Fedorov", "Brendan Shanahan"]'::jsonb, 1,
 'Steve Yzerman won the Conn Smythe in 1998 as Detroit repeated as Stanley Cup champions.', 'curated'),

('2026-10-03', 'hard', 'nhl',
 'In what year did Wayne Gretzky win his final Stanley Cup and Conn Smythe Trophy with the Edmonton Oilers, just before being traded?',
 '["1990", "1985", "1988", "1987"]'::jsonb, 2,
 'Gretzky won his fourth Cup and second Conn Smythe with Edmonton in 1988, months before the trade to Los Angeles.', 'curated'),

('2026-10-04', 'hard', 'nhl',
 'Which player won the 2009 Conn Smythe Trophy as Pittsburgh avenged a Final loss to Detroit from the year before?',
 '["Kris Letang", "Evgeni Malkin", "Sidney Crosby", "Marc-Andre Fleury"]'::jsonb, 1,
 'Evgeni Malkin won the 2009 Conn Smythe as the Penguins beat Detroit in a Final rematch after losing to them in 2008.', 'curated'),

('2026-10-05', 'hard', 'nhl',
 'Who was the first-ever first overall pick in NHL Entry Draft history, selected by Montreal in 1963?',
 '["Gilbert Perreault", "Guy Lafleur", "Garry Monahan", "Denis Potvin"]'::jsonb, 2,
 'Garry Monahan, selected by Montreal in 1963, was the first player ever chosen first overall in the NHL draft.', 'curated'),

('2026-10-06', 'hard', 'nhl',
 'Which Hall of Famer was the first overall pick of the 1971 NHL Draft, taken by Montreal?',
 '["Guy Lafleur", "Larry Robinson", "Marcel Dionne", "Rick Martin"]'::jsonb, 0,
 'Guy Lafleur was drafted first overall by Montreal in 1971 and became a franchise legend.', 'curated'),

('2026-10-07', 'hard', 'nhl',
 'Which Hall of Fame defenseman was drafted first overall by the New York Islanders in 1973?',
 '["Denis Potvin", "Clark Gillies", "Bryan Trottier", "Mike Bossy"]'::jsonb, 0,
 'Denis Potvin was drafted first overall by the Islanders in 1973 and later captained their four-Cup dynasty.', 'curated'),

('2026-10-08', 'hard', 'nhl',
 'Which Hall of Famer was drafted first overall by the Winnipeg Jets in 1981?',
 '["Bobby Smith", "Ron Francis", "Doug Wickenheiser", "Dale Hawerchuk"]'::jsonb, 3,
 'Dale Hawerchuk was drafted first overall by the original Winnipeg Jets in 1981.', 'curated'),

('2026-10-09', 'hard', 'nhl',
 'Which player was the first overall pick of the 1984 NHL Draft, chosen by Pittsburgh?',
 '["Mario Lemieux", "Kirk Muller", "Al Iafrate", "Doug Bodger"]'::jsonb, 0,
 'Mario Lemieux was drafted first overall by Pittsburgh in 1984.', 'curated'),

('2026-10-10', 'hard', 'nhl',
 'Which future top American-born scorer was drafted first overall by the Minnesota North Stars in 1988?',
 '["Mike Modano", "Tony Amonte", "Rod Brind''Amour", "Jeremy Roenick"]'::jsonb, 0,
 'Mike Modano was drafted first overall by the Minnesota North Stars in 1988.', 'curated'),

('2026-10-11', 'hard', 'nhl',
 'Who became the first European-born player selected first overall in an NHL draft, in 1989?',
 '["Teemu Selanne", "Peter Forsberg", "Nicklas Lidstrom", "Mats Sundin"]'::jsonb, 3,
 'Mats Sundin, drafted first overall by Quebec in 1989, was the first European-born player taken first overall.', 'curated'),

('2026-10-12', 'hard', 'nhl',
 'Which team drafted Eric Lindros first overall in 1991, only to see him refuse to ever play for them?',
 '["Philadelphia Flyers", "Toronto Maple Leafs", "Quebec Nordiques", "New York Rangers"]'::jsonb, 2,
 'Lindros refused to report to Quebec after being drafted first overall in 1991, forcing a trade to Philadelphia in 1992.', 'curated'),

('2026-10-13', 'hard', 'nhl',
 'Which player, drafted first overall in 1993 by Ottawa, is often cited as one of the biggest draft busts in NHL history?',
 '["Chris Gratton", "Alexandre Daigle", "Chris Pronger", "Rob Niedermayer"]'::jsonb, 1,
 'Alexandre Daigle went first overall to Ottawa in 1993 but never lived up to the pick, becoming a cautionary draft tale.', 'curated'),

('2026-10-14', 'hard', 'nhl',
 'Which player was drafted first overall by Boston in 1997?',
 '["Patrick Marleau", "Olli Jokinen", "Roberto Luongo", "Joe Thornton"]'::jsonb, 3,
 'Joe Thornton was drafted first overall by Boston in 1997.', 'curated'),

('2026-10-15', 'hard', 'nhl',
 'Which player was drafted first overall by Washington in 2004?',
 '["Evgeni Malkin", "Andrew Ladd", "Cam Barker", "Alexander Ovechkin"]'::jsonb, 3,
 'Alexander Ovechkin was drafted first overall by Washington in 2004.', 'curated'),

('2026-10-16', 'hard', 'nhl',
 'Which player was the first overall pick of the 2005 NHL Draft, held after the lockout wiped out the previous season?',
 '["Sidney Crosby", "Benoit Pouliot", "Bobby Ryan", "Jack Johnson"]'::jsonb, 0,
 'Sidney Crosby was drafted first overall by Pittsburgh in 2005, the draft immediately following the 2004-05 lockout.', 'curated'),

('2026-10-17', 'hard', 'nhl',
 'Which player was drafted first overall by Edmonton in 2015?',
 '["Noah Hanifin", "Jack Eichel", "Connor McDavid", "Mitch Marner"]'::jsonb, 2,
 'Connor McDavid was drafted first overall by Edmonton in 2015.', 'curated'),

('2026-10-18', 'hard', 'nhl',
 'Which player was drafted first overall by Chicago in 2023?',
 '["Adam Fantilli", "Connor Bedard", "Matvei Michkov", "Leo Carlsson"]'::jsonb, 1,
 'Connor Bedard was drafted first overall by Chicago in 2023.', 'curated'),

('2026-10-19', 'hard', 'nhl',
 'Which player was drafted first overall by San Jose in 2024?',
 '["Macklin Celebrini", "Artyom Levshunov", "Ivan Demidov", "Cayden Lindstrom"]'::jsonb, 0,
 'Macklin Celebrini was drafted first overall by San Jose in 2024.', 'curated'),

-- ============================ PWHL (13) ============================
('2026-08-25', 'hard', 'pwhl',
 'What is the name of the PWHL''s championship trophy, first awarded in 2024?',
 '["Riveters Cup", "Isobel Cup", "Walter Cup", "Clarkson Cup"]'::jsonb, 2,
 'The Walter Cup, named for PWHL owner Mark Walter, has been the league''s championship trophy since 2024 -- distinct from the Isobel Cup, which belongs to the separate, now-defunct PHF.', 'curated'),

('2026-08-26', 'hard', 'pwhl',
 'Who scored the first-ever goal in PWHL history, on January 1, 2024?',
 '["Taylor Heise", "Brianne Jenner", "Emily Clark", "Ella Shelton"]'::jsonb, 3,
 'Ella Shelton scored the first goal in PWHL history for New York in a 4-0 win over Toronto on opening night.', 'curated'),

('2026-08-27', 'hard', 'pwhl',
 'Which team won the Walter Cup in both of the PWHL''s first two seasons, 2024 and 2024-25?',
 '["Boston Fleet", "Minnesota Frost", "Ottawa Charge", "Toronto Sceptres"]'::jsonb, 1,
 'The Minnesota Frost won the Walter Cup in the PWHL''s first two seasons.', 'curated'),

('2026-08-28', 'hard', 'pwhl',
 'Which team became the first Canadian franchise to win the Walter Cup, in 2025-26?',
 '["Montreal Victoire", "Ottawa Charge", "Minnesota Frost", "Toronto Sceptres"]'::jsonb, 0,
 'Montreal Victoire won the 2025-26 Walter Cup, the first Canadian team to do so.', 'curated'),

('2026-08-29', 'hard', 'pwhl',
 'Who was the first overall pick in the PWHL''s inaugural 2023 draft?',
 '["Ella Shelton", "Taylor Heise", "Erin Ambrose", "Jocelyne Larocque"]'::jsonb, 1,
 'Taylor Heise was selected first overall by Minnesota in the PWHL''s inaugural September 2023 draft.', 'curated'),

('2026-08-30', 'hard', 'pwhl',
 'Which retired tennis legend announced the first overall pick of the PWHL''s inaugural 2023 draft?',
 '["Chris Evert", "Martina Navratilova", "Venus Williams", "Billie Jean King"]'::jsonb, 3,
 'Billie Jean King, a PWHL advisor, announced Taylor Heise as the draft''s first overall pick in September 2023.', 'curated'),

('2026-08-31', 'hard', 'pwhl',
 'Which two cities'' teams played in the PWHL''s first-ever game, on January 1, 2024?',
 '["Ottawa and Minnesota", "Toronto and Montreal", "Toronto and New York", "Boston and Montreal"]'::jsonb, 2,
 'Toronto hosted New York in the PWHL''s first-ever game on January 1, 2024, at the Mattamy Athletic Centre.', 'curated'),

('2026-09-01', 'hard', 'pwhl',
 'The PWHL''s April 2024 game between Montreal and Toronto drew over 21,000 fans, setting what kind of record?',
 '["The all-time women''s hockey attendance record", "The fastest sellout in league history", "The largest road-team crowd in NHL arena history", "The PWHL''s TV ratings record"]'::jsonb, 0,
 'The 21,105 fans at Montreal''s "Duel at the Top" vs. Toronto in April 2024 set the all-time attendance record for a women''s hockey game.', 'curated'),

('2026-09-02', 'hard', 'pwhl',
 'Which two markets joined the PWHL as expansion teams for the 2025-26 season?',
 '["Las Vegas and San Jose", "Detroit and Hamilton", "Vancouver and Seattle", "Buffalo and Pittsburgh"]'::jsonb, 2,
 'Vancouver and Seattle joined the PWHL for the 2025-26 season, growing the league to eight teams.', 'curated'),

('2026-09-03', 'hard', 'pwhl',
 'How many teams will the PWHL have starting the 2026-27 season, after Detroit, Hamilton, Las Vegas, and San Jose join?',
 '["10", "14", "12", "16"]'::jsonb, 2,
 'The 2026-27 expansion wave brings the PWHL to 12 teams total.', 'curated'),

('2026-09-04', 'hard', 'pwhl',
 'Which ownership group wholly owns and operates the PWHL?',
 '["Disney", "NHL Enterprises", "Billie Jean King Enterprises", "Mark Walter Group"]'::jsonb, 3,
 'The Mark Walter Group, which also owns the LA Dodgers, wholly owns and operates the PWHL.', 'curated'),

('2026-09-05', 'hard', 'pwhl',
 'The PWHL''s formation followed a boycott by which players'' association, after a predecessor league folded in 2019?',
 '["NWHL", "PWHPA", "PHF", "CWHL"]'::jsonb, 1,
 'The Professional Women''s Hockey Players Association (PWHPA) boycotted pro play after the CWHL folded in 2019, eventually leading to the PWHL''s creation.', 'curated'),

('2026-09-06', 'hard', 'pwhl',
 'The PWHL''s six original teams played their entire inaugural 2023-24 season without official nicknames -- in what season did names like the Boston Fleet and Toronto Sceptres debut?',
 '["2024-25", "2023-24", "2025-26", "2026-27"]'::jsonb, 0,
 'The PWHL''s charter teams played the 2023-24 season unnamed, receiving nicknames like Boston Fleet and Toronto Sceptres starting in 2024-25.', 'curated')

on conflict (question_date, tier, sport, team) do nothing;
